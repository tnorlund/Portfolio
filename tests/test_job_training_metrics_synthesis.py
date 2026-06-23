"""Tests for training metrics synthetic receipt evidence summaries."""

import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "infra"
    / "routes"
    / "job_training_metrics"
    / "handler"
    / "index.py"
)


class FakePaginator:
    def paginate(self, Bucket, Prefix):  # noqa: N803 - boto3 style
        assert Bucket == "pattern-bucket"
        assert Prefix == "line_item_patterns/run-1/"
        return [
            {
                "Contents": [
                    {"Key": "line_item_patterns/run-1/a.json"},
                    {"Key": "line_item_patterns/run-1/readme.txt"},
                    {"Key": "line_item_patterns/run-1/b.json"},
                ]
            }
        ]


class FakeS3:
    def __init__(self):
        self.objects = {
            "line_item_patterns/run-1/a.json": {"merchant_name": "Sprouts"},
            "line_item_patterns/run-1/b.json": {"merchant_name": "Trader Joe's"},
        }

    def get_paginator(self, operation_name):
        assert operation_name == "list_objects_v2"
        return FakePaginator()

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3 style
        assert Bucket == "pattern-bucket"
        return {"Body": io.BytesIO(json.dumps(self.objects[Key]).encode("utf-8"))}


def _load_module(monkeypatch):
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "test-table")

    import boto3

    monkeypatch.setattr(boto3, "client", lambda service_name: FakeS3())
    spec = importlib.util.spec_from_file_location(
        "job_training_metrics_handler_for_test",
        MODULE_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_pattern_artifacts_accepts_s3_prefix(monkeypatch):
    module = _load_module(monkeypatch)

    artifacts = module._load_pattern_artifacts(
        {
            "bucket": "pattern-bucket",
            "prefix": "line_item_patterns/run-1/",
        }
    )

    assert [artifact["merchant_name"] for artifact in artifacts] == [
        "Sprouts",
        "Trader Joe's",
    ]


def test_summarize_synthesis_artifacts_bounds_candidate_evidence(monkeypatch):
    module = _load_module(monkeypatch)

    artifact = {
        "merchant_name": "Sprouts Farmers Market",
        "source_receipt_quality": {
            "merchant_name": "Sprouts Farmers Market",
            "status": "usable",
            "receipt_count": 12,
            "receipts_with_lines": 12,
            "receipts_with_words": 12,
            "receipts_with_labels": 12,
            "receipts_with_merchant_name_label": 12,
            "receipts_with_line_item_labels": 9,
            "receipts_with_grand_total_label": 10,
            "receipts_with_date_or_time_label": 11,
            "line_count": 468,
            "word_count": 1280,
            "labeled_word_count": 180,
            "top_labels": {
                "PRODUCT_NAME": 64,
                "LINE_TOTAL": 48,
                "MERCHANT_NAME": 24,
            },
            "blockers": [],
            "limitations": [],
        },
        "synthetic_receipt_plan": {"recipes": [{}, {}]},
        "merchant_receipt_parameterization": {
            "merchant_name": "Sprouts Farmers Market",
            "receipt_count": 12,
            "category_patterns": {"heading_counts": {"PRODUCE": 8, "DAIRY": 6}},
            "observed_item_catalog": [
                {
                    "product_text": "YELLOW BANANAS",
                    "category": "PRODUCE",
                    "observed_count": 3,
                }
            ],
            "synthesis_readiness": {
                "merchant_name": "Sprouts Farmers Market",
                "status": "ready",
                "score": 0.91,
                "supported_operations": [
                    "hard_negative",
                    "add_line_item",
                    "remove_line_item",
                ],
                "candidate_capacity": 5,
                "catalog_item_count": 18,
                "category_count": 5,
                "grounded_add_item_candidate_count": 4,
                "removable_item_candidate_count": 2,
                "blockers": [],
                "limitations": ["no_taxable_item_synthesis"],
            },
        },
        "synthetic_receipt_candidates": [
            {
                "candidate_id": "synthetic-sprouts-bananas",
                "metadata": {
                    "source": "sprouts_arithmetic_geometry",
                    "operation": "add_line_item",
                    "added_item": {
                        "product_text": "YELLOW BANANAS",
                        "category": "PRODUCE",
                        "line_total": "1.95",
                        "seen_in_other_receipt": True,
                        "source_receipt_keys": ["source#00001"],
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
                    "structure_similarity": {
                        "score": 0.94,
                        "nearest_real_receipt_key": "neighbor#00001",
                        "components": {
                            "category_sequence": 0.90,
                            "category_set": 1.00,
                            "item_count": 0.86,
                        },
                    },
                    "arithmetic_reconciliation": {
                        "summary_update_policy": "non_taxable_item_delta",
                        "old_subtotal": "8.49",
                        "new_subtotal": "10.44",
                        "old_grand_total": "8.49",
                        "new_grand_total": "10.44",
                        "subtotal_delta": "1.95",
                        "grand_total_delta": "1.95",
                        "tax_delta": "0.00",
                        "tax_policy": "left unchanged because synthesized item is non-taxable",
                        "updated_summary_labels": {
                            "subtotal": 1,
                            "grand_total": 1,
                            "payment_or_balance": 1,
                        },
                    },
                },
                "tokens": ["YELLOW", "BANANAS", "1.95"],
                "bboxes": [[1, 2, 3, 4]],
            },
            {
                "candidate_id": "synthetic-sprouts-hard-negative",
                "metadata": {
                    "source": "sprouts_parameterized_geometry",
                    "operation": "hard_negative",
                    "actual_label": "O",
                    "predicted_label": "ADDRESS_LINE",
                },
                "tokens": ["LOCAL", "FAVORITES"],
            },
        ],
    }

    summary = module._summarize_synthesis_artifacts(
        [artifact],
        artifact_ref={"s3_uri": "s3://pattern-bucket/line_item_patterns/run-1/"},
        synthetic_train_examples=4,
    )

    assert summary["status"] == "available"
    assert summary["candidate_count"] == 2
    assert summary["recipe_count"] == 2
    assert summary["grounded_candidate_count"] == 1
    assert summary["grounded_candidate_share"] == 0.5
    assert summary["best_structure_similarity"] == 0.94
    assert summary["avg_structure_components"] == {
        "category_sequence": 0.9,
        "category_set": 1.0,
        "item_count": 0.86,
    }
    assert summary["arithmetic_candidate_count"] == 1
    assert summary["non_taxable_arithmetic_candidate_count"] == 1
    assert summary["arithmetic_update_counts"] == {
        "subtotal": 1,
        "grand_total": 1,
        "payment_or_balance": 1,
    }
    assert summary["profile_receipt_count"] == 12
    assert summary["catalog_item_count"] == 1
    assert summary["category_count"] == 2
    assert summary["readiness_status_counts"] == {"ready": 1}
    assert summary["ready_merchant_count"] == 1
    assert summary["avg_readiness_score"] == 0.91
    assert summary["source_receipt_quality"] == {
        "merchant_count": 1,
        "usable_merchant_count": 1,
        "limited_merchant_count": 0,
        "blocked_merchant_count": 0,
        "status_counts": {"usable": 1},
        "receipt_count": 12,
        "labeled_word_count": 180,
        "merchants": [
            {
                "merchant_name": "Sprouts Farmers Market",
                "status": "usable",
                "receipt_count": 12,
                "receipts_with_lines": 12,
                "receipts_with_words": 12,
                "receipts_with_labels": 12,
                "receipts_with_merchant_name_label": 12,
                "receipts_with_line_item_labels": 9,
                "receipts_with_grand_total_label": 10,
                "receipts_with_date_or_time_label": 11,
                "line_count": 468,
                "word_count": 1280,
                "labeled_word_count": 180,
                "top_labels": {
                    "PRODUCT_NAME": 64,
                    "LINE_TOTAL": 48,
                    "MERCHANT_NAME": 24,
                },
            }
        ],
    }
    assert summary["merchant_readiness"] == [
        {
            "merchant_name": "Sprouts Farmers Market",
            "status": "ready",
            "score": 0.91,
            "supported_operations": [
                "hard_negative",
                "add_line_item",
                "remove_line_item",
            ],
            "candidate_capacity": 5,
            "catalog_item_count": 18,
            "category_count": 5,
            "grounded_add_item_candidate_count": 4,
            "removable_item_candidate_count": 2,
            "blockers": [],
            "limitations": ["no_taxable_item_synthesis"],
        }
    ]
    assert summary["top_catalog_items"] == [
        {
            "product_text": "YELLOW BANANAS",
            "category": "PRODUCE",
            "observed_count": 3,
        }
    ]
    assert summary["candidate_examples"][0] == {
        "candidate_id": "synthetic-sprouts-bananas",
        "merchant_name": "Sprouts Farmers Market",
        "source": "sprouts_arithmetic_geometry",
        "operation": "add_line_item",
        "actual_label": None,
        "predicted_label": None,
        "item_text": "YELLOW BANANAS",
        "category": "PRODUCE",
        "line_total": "1.95",
        "seen_in_other_receipt": True,
        "evidence_receipts": ["source#00001"],
        "structure_similarity": 0.94,
        "nearest_real_receipt_key": "neighbor#00001",
    }
    assert "tokens" not in summary["candidate_examples"][0]
    assert "bboxes" not in summary["candidate_examples"][0]


def test_summarize_synthesis_artifacts_exposes_field_replacement_evidence(
    monkeypatch,
):
    module = _load_module(monkeypatch)

    candidate = {
        "candidate_id": "synthetic-sprouts-date",
        "metadata": {
            "source": "merchant_mutable_field_geometry",
            "operation": "replace_field",
            "field_replacement": {
                "label": "DATE",
                "old_text": "05/13/2026",
                "new_text": "05/14/2026",
                "format": "MM/DD/YYYY",
            },
            "mutable_field_evidence": {
                "safe_to_mutate": True,
                "observed_count": 4,
                "stable_format": "MM/DD/YYYY",
                "stable_geometry": True,
            },
            "structure_similarity": {
                "score": 0.96,
                "nearest_real_receipt_key": "neighbor#00002",
            },
        },
        "tokens": ["05/14/2026"],
        "bboxes": [[1, 2, 3, 4]],
    }
    artifact = {
        "merchant_name": "Sprouts Farmers Market",
        "synthetic_receipt_candidates": [candidate],
    }

    summary = module._summarize_synthesis_artifacts(
        [artifact],
        artifact_ref={"s3_uri": "s3://pattern-bucket/line_item_patterns/run-1/"},
        synthetic_train_examples=1,
    )

    assert summary["operation_counts"] == {"replace_field": 1}
    assert summary["field_replacement_counts"] == {"DATE": 1}
    assert summary["candidate_examples"][0] == {
        "candidate_id": "synthetic-sprouts-date",
        "merchant_name": "Sprouts Farmers Market",
        "source": "merchant_mutable_field_geometry",
        "operation": "replace_field",
        "actual_label": None,
        "predicted_label": None,
        "item_text": None,
        "category": None,
        "line_total": None,
        "seen_in_other_receipt": None,
        "field_label": "DATE",
        "old_text": "05/13/2026",
        "new_text": "05/14/2026",
        "field_format": "MM/DD/YYYY",
        "field_observed_count": 4,
        "evidence_receipts": [],
        "structure_similarity": 0.96,
        "nearest_real_receipt_key": "neighbor#00002",
    }
    assert "tokens" not in summary["candidate_examples"][0]
    assert "bboxes" not in summary["candidate_examples"][0]


def test_summarize_synthesis_bundle_exposes_candidate_mix(monkeypatch):
    module = _load_module(monkeypatch)

    bundle = {
        "schema_version": "layoutlm-synthetic-training-bundle-v1",
        "validation_policy": "real_receipts_only",
        "preflight": {
            "llm_execution": {
                "mode_counts": {"deterministic_fallback": 3},
                "paid_llm_disabled_count": 3,
                "api_call_allowed_count": 0,
                "configured_models": ["openai/gpt-5.5"],
                "latest_model_sources": [
                    "https://developers.openai.com/api/docs/guides/latest-model"
                ],
                "latest_model_verified_at": "2026-06-23",
            }
        },
        "source_receipt_quality": {
            "merchant_count": 3,
            "usable_merchant_count": 2,
            "limited_merchant_count": 0,
            "blocked_merchant_count": 1,
            "status_counts": {"usable": 2, "blocked": 1},
            "receipt_count": 17,
            "labeled_word_count": 236,
            "merchants": [
                {
                    "merchant_name": "Sprouts Farmers Market",
                    "status": "usable",
                    "receipt_count": 12,
                    "receipts_with_lines": 12,
                    "receipts_with_words": 12,
                    "receipts_with_labels": 12,
                    "receipts_with_merchant_name_label": 12,
                    "receipts_with_line_item_labels": 9,
                    "receipts_with_grand_total_label": 10,
                    "receipts_with_date_or_time_label": 11,
                    "line_count": 468,
                    "word_count": 1280,
                    "labeled_word_count": 180,
                    "top_labels": {
                        "PRODUCT_NAME": 64,
                        "LINE_TOTAL": 48,
                        "MERCHANT_NAME": 24,
                    },
                    "blockers": [],
                    "limitations": [],
                },
                {
                    "merchant_name": "Market Mart",
                    "status": "usable",
                    "receipt_count": 4,
                    "receipts_with_lines": 4,
                    "receipts_with_words": 4,
                    "receipts_with_labels": 4,
                    "receipts_with_merchant_name_label": 4,
                    "receipts_with_line_item_labels": 4,
                    "receipts_with_grand_total_label": 4,
                    "receipts_with_date_or_time_label": 3,
                    "line_count": 144,
                    "word_count": 420,
                    "labeled_word_count": 56,
                    "top_labels": {"PRODUCT_NAME": 20, "LINE_TOTAL": 14},
                    "blockers": [],
                    "limitations": [],
                },
                {
                    "merchant_name": "Thin Merchant",
                    "status": "blocked",
                    "receipt_count": 1,
                    "receipts_with_lines": 1,
                    "receipts_with_words": 1,
                    "receipts_with_labels": 0,
                    "receipts_with_merchant_name_label": 0,
                    "receipts_with_line_item_labels": 0,
                    "receipts_with_grand_total_label": 0,
                    "receipts_with_date_or_time_label": 0,
                    "line_count": 18,
                    "word_count": 64,
                    "labeled_word_count": 0,
                    "top_labels": {},
                    "blockers": ["no_word_labels"],
                    "limitations": ["no_labeled_line_items"],
                },
            ],
        },
        "selection": {
            "candidates_seen": 5,
            "candidates_accepted": 3,
            "candidates_rejected": 2,
            "rejection_reasons": {
                "merchant_operation_synthetic_cap": 1,
                "merchant_synthesis_not_ready": 1,
            },
            "max_per_merchant": 5,
            "max_per_merchant_operation": 2,
            "min_structure_similarity": 0.6,
            "structure_component_thresholds": {
                "price_column": 0.75,
                "line_step": 0.45,
                "category_sequence": 0.4,
                "category_set": 0.4,
                "token_count": 0.35,
            },
        },
        "candidate_mix": {
            "candidate_count": 5,
            "accepted_count": 3,
            "rejected_count": 2,
            "rejection_reasons": {
                "merchant_operation_synthetic_cap": 1,
                "merchant_synthesis_not_ready": 1,
            },
            "merchant_count": 3,
            "accepted_merchant_count": 2,
            "operation_counts": {
                "add_line_item": 2,
                "hard_negative": 3,
            },
            "accepted_operation_counts": {
                "add_line_item": 2,
                "hard_negative": 1,
            },
            "category_counts": {"PRODUCE": 2},
            "accepted_category_counts": {"PRODUCE": 2},
            "accepted_structure_similarity": {
                "count": 3,
                "avg": 0.91,
                "min": 0.86,
                "max": 0.94,
            },
            "accepted_structure_components": {
                "price_column": {
                    "count": 3,
                    "avg": 0.98,
                    "min": 0.94,
                    "max": 1.0,
                },
                "line_step": {
                    "count": 3,
                    "avg": 0.67,
                    "min": 0.55,
                    "max": 0.78,
                },
            },
            "accepted_real_baseline_comparison": {
                "count": 1,
                "within_real_score_range_count": 1,
                "below_real_score_range_count": 0,
                "within_real_score_range_share": 1.0,
                "candidate_score": {
                    "count": 1,
                    "avg": 0.94,
                    "min": 0.94,
                    "max": 0.94,
                },
                "baseline_avg": {
                    "count": 1,
                    "avg": 0.91,
                    "min": 0.91,
                    "max": 0.91,
                },
                "baseline_min": {
                    "count": 1,
                    "avg": 0.82,
                    "min": 0.82,
                    "max": 0.82,
                },
                "baseline_pair_count": {
                    "count": 1,
                    "avg": 66,
                    "min": 66,
                    "max": 66,
                },
                "delta_from_avg": {
                    "count": 1,
                    "avg": 0.03,
                    "min": 0.03,
                    "max": 0.03,
                },
                "delta_from_min": {
                    "count": 1,
                    "avg": 0.12,
                    "min": 0.12,
                    "max": 0.12,
                },
            },
            "accepted_candidate_quality": {
                "count": 2,
                "avg": 0.94,
                "min": 0.93,
                "max": 0.95,
            },
            "accepted_candidate_quality_components": {
                "cross_receipt_grounding": {
                    "count": 2,
                    "avg": 1.0,
                    "min": 1.0,
                    "max": 1.0,
                },
                "structure_similarity": {
                    "count": 2,
                    "avg": 0.9,
                    "min": 0.86,
                    "max": 0.94,
                },
            },
            "accepted_grounded_candidate_count": 2,
            "accepted_arithmetic_candidate_count": 2,
            "merchants": [
                {
                    "merchant_name": "Sprouts Farmers Market",
                    "candidate_count": 2,
                    "accepted_count": 2,
                    "rejected_count": 0,
                    "rejection_reasons": {},
                    "accepted_operation_counts": {
                        "add_line_item": 1,
                        "hard_negative": 1,
                    },
                    "accepted_category_counts": {"PRODUCE": 1},
                    "accepted_grounded_candidate_count": 1,
                    "accepted_arithmetic_candidate_count": 1,
                    "accepted_structure_similarity": {
                        "count": 2,
                        "avg": 0.92,
                        "min": 0.9,
                        "max": 0.94,
                    },
                    "accepted_real_baseline_comparison": {
                        "count": 1,
                        "within_real_score_range_count": 1,
                        "below_real_score_range_count": 0,
                        "within_real_score_range_share": 1.0,
                        "candidate_score": {
                            "count": 1,
                            "avg": 0.94,
                            "min": 0.94,
                            "max": 0.94,
                        },
                        "baseline_avg": {
                            "count": 1,
                            "avg": 0.91,
                            "min": 0.91,
                            "max": 0.91,
                        },
                        "baseline_min": {
                            "count": 1,
                            "avg": 0.82,
                            "min": 0.82,
                            "max": 0.82,
                        },
                        "baseline_pair_count": {
                            "count": 1,
                            "avg": 66,
                            "min": 66,
                            "max": 66,
                        },
                        "delta_from_avg": {
                            "count": 1,
                            "avg": 0.03,
                            "min": 0.03,
                            "max": 0.03,
                        },
                        "delta_from_min": {
                            "count": 1,
                            "avg": 0.12,
                            "min": 0.12,
                            "max": 0.12,
                        },
                    },
                    "accepted_candidate_quality": {
                        "count": 1,
                        "avg": 0.95,
                        "min": 0.95,
                        "max": 0.95,
                    },
                    "accepted_candidate_quality_components": {
                        "cross_receipt_grounding": {
                            "count": 1,
                            "avg": 1.0,
                            "min": 1.0,
                            "max": 1.0,
                        }
                    },
                    "accepted_candidate_ids": ["sprouts-add", "sprouts-hard"],
                },
                {
                    "merchant_name": "Market Mart",
                    "candidate_count": 2,
                    "accepted_count": 1,
                    "rejected_count": 1,
                    "rejection_reasons": {"merchant_operation_synthetic_cap": 1},
                    "accepted_operation_counts": {"add_line_item": 1},
                    "accepted_category_counts": {"PRODUCE": 1},
                    "accepted_grounded_candidate_count": 1,
                    "accepted_arithmetic_candidate_count": 1,
                    "accepted_structure_similarity": {
                        "count": 1,
                        "avg": 0.86,
                        "min": 0.86,
                        "max": 0.86,
                    },
                    "accepted_candidate_ids": ["market-add"],
                },
                {
                    "merchant_name": "Thin Merchant",
                    "candidate_count": 1,
                    "accepted_count": 0,
                    "rejected_count": 1,
                    "rejection_reasons": {"merchant_synthesis_not_ready": 1},
                    "accepted_operation_counts": {},
                    "accepted_category_counts": {},
                    "accepted_grounded_candidate_count": 0,
                    "accepted_arithmetic_candidate_count": 0,
                    "accepted_structure_similarity": {
                        "count": 0,
                        "avg": None,
                        "min": None,
                        "max": None,
                    },
                    "accepted_candidate_ids": [],
                },
            ],
        },
        "merchant_synthesis_contracts": [
            {
                "merchant_name": "Sprouts Farmers Market",
                "status": "ready",
                "score": 0.91,
                "source_receipt_count": 12,
                "supported_operations": [
                    "hard_negative",
                    "add_line_item",
                    "replace_field",
                ],
                "operation_contracts": {
                    "hard_negative": {"ready": True},
                    "add_line_item": {"ready": True},
                    "replace_field": {
                        "ready": True,
                        "fields": {
                            "DATE": {"safe_to_mutate": True},
                            "TIME": {"safe_to_mutate": True},
                        },
                    },
                },
                "bundle_acceptance": {
                    "accepted_operation_counts": {
                        "add_line_item": 1,
                        "hard_negative": 1,
                    },
                    "accepted_category_counts": {"PRODUCE": 1},
                    "accepted_field_replacement_counts": {"DATE": 1, "TIME": 1},
                },
                "tax_contract": {
                    "supported_policy": "non_taxable_item_delta",
                    "taxable_item_count": 2,
                    "tax_rate_observation_count": 2,
                    "stable_tax_rate": True,
                    "avg_tax_rate_percent": "7.78%",
                    "tax_changing_synthesis_ready": False,
                    "tax_changing_synthesis_blockers": [
                        "tax_changing_loader_gate_not_enabled"
                    ],
                },
                "blockers": [],
                "limitations": [],
            },
            {
                "merchant_name": "Market Mart",
                "status": "ready",
                "score": 0.86,
                "source_receipt_count": 4,
                "supported_operations": ["add_line_item"],
                "operation_contracts": {
                    "add_line_item": {"ready": True},
                    "replace_field": {"ready": False, "fields": {}},
                },
                "bundle_acceptance": {
                    "accepted_operation_counts": {"add_line_item": 1},
                    "accepted_category_counts": {"PRODUCE": 1},
                    "accepted_field_replacement_counts": {},
                },
                "blockers": [],
                "limitations": ["no_mutable_fields"],
            },
            {
                "merchant_name": "Thin Merchant",
                "status": "blocked",
                "score": 0.2,
                "source_receipt_count": 1,
                "supported_operations": [],
                "operation_contracts": {},
                "bundle_acceptance": {
                    "accepted_operation_counts": {},
                    "accepted_category_counts": {},
                    "accepted_field_replacement_counts": {},
                },
                "blockers": ["no_line_items"],
                "limitations": [],
            },
        ],
        "synthetic_training_examples": [
            {
                "candidate_id": "sprouts-add",
                "merchant_name": "Sprouts Farmers Market",
                "metadata": {
                    "source": "sprouts_arithmetic_geometry",
                    "operation": "add_line_item",
                    "added_item": {
                        "product_text": "YELLOW BANANAS",
                        "category": "PRODUCE",
                        "line_total": "1.95",
                        "seen_in_other_receipt": True,
                        "source_receipt_keys": ["source#00001"],
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
                    "structure_similarity": {"score": 0.94},
                    "candidate_quality": {
                        "score": 0.95,
                        "high_fidelity": True,
                        "components": {
                            "arithmetic_reconciliation": 0.9,
                            "category_alignment": 1.0,
                            "cross_receipt_grounding": 1.0,
                            "layout_integrity": 1.0,
                            "structure_similarity": 0.94,
                            "token_budget": 1.0,
                        },
                    },
                    "selection_evidence": {
                        "schema_version": "synthetic-candidate-selection-v1",
                        "selected_from_candidate_count": 4,
                        "selected_input_index": 2,
                        "ranked_by": [
                            "candidate_quality.high_fidelity",
                            "real_baseline_comparison.within_real_score_range",
                            "candidate_quality.score",
                        ],
                        "selected_score": {
                            "candidate_quality": 0.95,
                            "high_fidelity": True,
                            "structure_similarity": 0.94,
                            "structure_component_pass_rate": 1.0,
                            "layout_integrity": 1.0,
                            "token_budget": 1.0,
                            "within_real_score_range": True,
                            "delta_from_min": 0.12,
                            "baseline_pair_count": 66,
                            "token_count": 9,
                        },
                        "selection_policy": (
                            "Generate feasible merchant-local mutations, then keep "
                            "the highest fidelity option instead of maximizing "
                            "synthetic volume."
                        ),
                    },
                    "synthetic_receipt_preview": {
                        "coordinate_system": "normalized_receipt_0_1000_y_high_is_top",
                        "line_count": 5,
                        "token_count": 9,
                        "truncated": False,
                        "text": "SPROUTS\nPRODUCE\nYELLOW BANANAS 1.95\nBALANCE DUE",
                        "lines": [
                            {
                                "line_number": 3,
                                "text": "YELLOW BANANAS 1.95",
                                "role": "line_item",
                                "synthetic_insert": True,
                                "modified_labels": [],
                            }
                        ],
                    },
                    "synthesis_accuracy_evidence": {
                        "operation": "add_line_item",
                        "checks": [
                            "item_seen_in_other_receipt",
                            "base_receipt_has_category",
                            "non_taxable_arithmetic_reconciled",
                        ],
                        "changed_text": "YELLOW BANANAS",
                        "category": "PRODUCE",
                        "tax_delta": "0.00",
                        "layout_integrity": {
                            "score": 1.0,
                            "passed": True,
                            "line_count": 5,
                            "word_count": 9,
                            "overlap_pair_count": 0,
                            "out_of_bounds_word_count": 0,
                            "invalid_word_box_count": 0,
                            "line_order_valid": True,
                        },
                        "structure_similarity": {
                            "score": 0.94,
                            "nearest_real_receipt_key": "receipt-b#00001",
                            "components": {
                                "price_column": 0.99,
                                "line_step": 0.95,
                                "token_count": 0.88,
                            },
                            "shape_deltas": {
                                "line_count_delta": 1,
                                "line_item_count_delta": 1,
                                "token_count_delta": 3,
                                "line_total_x_delta": 0,
                            },
                            "match_summary": {
                                "matched_components": [
                                    "price_column",
                                    "line_step",
                                    "token_count",
                                ],
                                "weak_components": [],
                                "shape_checks": [
                                    "price_column_aligned",
                                    "line_spacing_close",
                                    "token_count_close",
                                ],
                            },
                            "real_baseline_comparison": {
                                "baseline_receipt_count": 12,
                                "baseline_pair_count": 66,
                                "candidate_score": 0.94,
                                "baseline_avg": 0.91,
                                "baseline_min": 0.82,
                                "baseline_max": 0.98,
                                "within_real_score_range": True,
                                "delta_from_avg": 0.03,
                                "delta_from_min": 0.12,
                            },
                        },
                        "catalog_grounding": {
                            "product_observed_count": 2,
                            "product_seen_receipt_count": 2,
                            "product_seen_outside_base_count": 1,
                            "product_seen_outside_base": ["receipt-b#00001"],
                            "category": "PRODUCE",
                            "category_seen_count": 4,
                            "category_heading_seen_count": 3,
                            "category_seen_in_receipts": [
                                "receipt-a#00001",
                                "receipt-b#00001",
                            ],
                        },
                        "category_placement": {
                            "category": "PRODUCE",
                            "insert_y": 665.5,
                            "shifted_lower_lines_by": 42,
                            "selection_reason": (
                                "observed in another Sprouts receipt with the same category"
                            ),
                            "base_receipt_has_category": True,
                            "category_seen_count": 4,
                            "category_heading_seen_count": 3,
                            "category_alignment": "same_category_as_base",
                        },
                    },
                },
                "tokens": ["YELLOW", "BANANAS", "1.95"],
            }
        ],
    }

    summary = module._summarize_synthesis_artifacts(
        [bundle],
        artifact_ref={"s3_uri": "s3://pattern-bucket/bundles/run-1.json"},
        synthetic_train_examples=3,
    )
    expected_real_baseline = {
        "count": 1,
        "within_real_score_range_count": 1,
        "below_real_score_range_count": 0,
        "within_real_score_range_share": 1.0,
        "candidate_score": {"count": 1, "avg": 0.94, "min": 0.94, "max": 0.94},
        "baseline_avg": {"count": 1, "avg": 0.91, "min": 0.91, "max": 0.91},
        "baseline_min": {"count": 1, "avg": 0.82, "min": 0.82, "max": 0.82},
        "baseline_pair_count": {"count": 1, "avg": 66.0, "min": 66.0, "max": 66.0},
        "delta_from_avg": {"count": 1, "avg": 0.03, "min": 0.03, "max": 0.03},
        "delta_from_min": {"count": 1, "avg": 0.12, "min": 0.12, "max": 0.12},
    }

    assert summary["status"] == "available"
    assert summary["artifact_schema_version"] == (
        "layoutlm-synthetic-training-bundle-v1"
    )
    assert summary["candidate_count"] == 5
    assert summary["rejected_count"] == 2
    assert summary["rejection_reasons"] == {
        "merchant_operation_synthetic_cap": 1,
        "merchant_synthesis_not_ready": 1,
    }
    assert summary["synthetic_train_examples"] == 3
    assert summary["merchant_count"] == 3
    assert summary["accepted_merchant_count"] == 2
    assert summary["operation_counts"] == {
        "add_line_item": 2,
        "hard_negative": 3,
    }
    assert summary["accepted_operation_counts"] == {
        "add_line_item": 2,
        "hard_negative": 1,
    }
    assert summary["category_counts"] == {"PRODUCE": 2}
    assert summary["accepted_category_counts"] == {"PRODUCE": 2}
    assert summary["accepted_structure_similarity"] == {
        "count": 3,
        "avg": 0.91,
        "min": 0.86,
        "max": 0.94,
    }
    assert summary["accepted_structure_components"] == {
        "price_column": {
            "count": 3,
            "avg": 0.98,
            "min": 0.94,
            "max": 1.0,
        },
        "line_step": {
            "count": 3,
            "avg": 0.67,
            "min": 0.55,
            "max": 0.78,
        },
    }
    assert summary["accepted_real_baseline_comparison"] == expected_real_baseline
    assert summary["accepted_candidate_quality"] == {
        "count": 2,
        "avg": 0.94,
        "min": 0.93,
        "max": 0.95,
    }
    assert summary["accepted_candidate_quality_components"] == {
        "cross_receipt_grounding": {
            "count": 2,
            "avg": 1.0,
            "min": 1.0,
            "max": 1.0,
        },
        "structure_similarity": {
            "count": 2,
            "avg": 0.9,
            "min": 0.86,
            "max": 0.94,
        },
    }
    assert summary["llm_execution"] == {
        "mode_counts": {"deterministic_fallback": 3},
        "paid_llm_disabled_count": 3,
        "api_call_allowed_count": 0,
        "configured_models": ["openai/gpt-5.5"],
        "latest_model_sources": [
            "https://developers.openai.com/api/docs/guides/latest-model"
        ],
        "latest_model_verified_at": "2026-06-23",
    }
    assert summary["source_receipt_quality"] == {
        "merchant_count": 3,
        "usable_merchant_count": 2,
        "limited_merchant_count": 0,
        "blocked_merchant_count": 1,
        "status_counts": {"usable": 2, "blocked": 1},
        "receipt_count": 17,
        "labeled_word_count": 236,
        "merchants": [
            {
                "merchant_name": "Sprouts Farmers Market",
                "status": "usable",
                "receipt_count": 12,
                "receipts_with_lines": 12,
                "receipts_with_words": 12,
                "receipts_with_labels": 12,
                "receipts_with_merchant_name_label": 12,
                "receipts_with_line_item_labels": 9,
                "receipts_with_grand_total_label": 10,
                "receipts_with_date_or_time_label": 11,
                "line_count": 468,
                "word_count": 1280,
                "labeled_word_count": 180,
                "top_labels": {
                    "PRODUCT_NAME": 64,
                    "LINE_TOTAL": 48,
                    "MERCHANT_NAME": 24,
                },
            },
            {
                "merchant_name": "Market Mart",
                "status": "usable",
                "receipt_count": 4,
                "receipts_with_lines": 4,
                "receipts_with_words": 4,
                "receipts_with_labels": 4,
                "receipts_with_merchant_name_label": 4,
                "receipts_with_line_item_labels": 4,
                "receipts_with_grand_total_label": 4,
                "receipts_with_date_or_time_label": 3,
                "line_count": 144,
                "word_count": 420,
                "labeled_word_count": 56,
                "top_labels": {"PRODUCT_NAME": 20, "LINE_TOTAL": 14},
            },
            {
                "merchant_name": "Thin Merchant",
                "status": "blocked",
                "receipt_count": 1,
                "receipts_with_lines": 1,
                "receipts_with_words": 1,
                "receipts_with_labels": 0,
                "receipts_with_merchant_name_label": 0,
                "receipts_with_line_item_labels": 0,
                "receipts_with_grand_total_label": 0,
                "receipts_with_date_or_time_label": 0,
                "line_count": 18,
                "word_count": 64,
                "labeled_word_count": 0,
                "blockers": ["no_word_labels"],
                "limitations": ["no_labeled_line_items"],
            },
        ],
    }
    assert summary["accepted_grounded_candidate_count"] == 2
    assert summary["accepted_arithmetic_candidate_count"] == 2
    assert summary["contract_merchant_count"] == 3
    assert summary["contract_ready_merchant_count"] == 2
    assert summary["contract_operation_counts"] == {
        "add_line_item": 2,
        "hard_negative": 1,
        "replace_field": 1,
    }
    assert summary["contract_field_replacement_counts"] == {
        "DATE": 1,
        "TIME": 1,
    }
    assert summary["merchant_synthesis_contracts"] == [
        {
            "merchant_name": "Sprouts Farmers Market",
            "status": "ready",
            "score": 0.91,
            "source_receipt_count": 12,
            "supported_operations": [
                "hard_negative",
                "add_line_item",
                "replace_field",
            ],
            "ready_operations": [
                "hard_negative",
                "add_line_item",
                "replace_field",
            ],
            "accepted_operation_counts": {
                "add_line_item": 1,
                "hard_negative": 1,
            },
            "accepted_category_counts": {"PRODUCE": 1},
            "accepted_field_replacement_counts": {"DATE": 1, "TIME": 1},
            "tax_contract": {
                "supported_policy": "non_taxable_item_delta",
                "taxable_item_count": 2,
                "tax_rate_observation_count": 2,
                "stable_tax_rate": True,
                "avg_tax_rate_percent": "7.78%",
                "tax_changing_synthesis_ready": False,
                "tax_changing_synthesis_blockers": [
                    "tax_changing_loader_gate_not_enabled"
                ],
            },
            "blockers": [],
            "limitations": [],
        },
        {
            "merchant_name": "Market Mart",
            "status": "ready",
            "score": 0.86,
            "source_receipt_count": 4,
            "supported_operations": ["add_line_item"],
            "ready_operations": ["add_line_item"],
            "accepted_operation_counts": {"add_line_item": 1},
            "accepted_category_counts": {"PRODUCE": 1},
            "accepted_field_replacement_counts": {},
            "blockers": [],
            "limitations": ["no_mutable_fields"],
        },
        {
            "merchant_name": "Thin Merchant",
            "status": "blocked",
            "score": 0.2,
            "source_receipt_count": 1,
            "supported_operations": [],
            "ready_operations": [],
            "accepted_operation_counts": {},
            "accepted_category_counts": {},
            "accepted_field_replacement_counts": {},
            "blockers": ["no_line_items"],
            "limitations": [],
        },
    ]
    assert summary["grounded_candidate_share"] == 0.4
    assert summary["bundle_candidates_seen"] == 5
    assert summary["bundle_candidates_accepted"] == 3
    assert summary["bundle_candidates_rejected"] == 2
    assert summary["bundle_rejection_reasons"] == {
        "merchant_operation_synthetic_cap": 1,
        "merchant_synthesis_not_ready": 1,
    }
    assert summary["candidate_mix_merchants"] == [
        {
            "merchant_name": "Sprouts Farmers Market",
            "candidate_count": 2,
            "accepted_count": 2,
            "rejected_count": 0,
            "rejection_reasons": {},
            "accepted_operation_counts": {
                "add_line_item": 1,
                "hard_negative": 1,
            },
            "accepted_category_counts": {"PRODUCE": 1},
            "accepted_grounded_candidate_count": 1,
            "accepted_arithmetic_candidate_count": 1,
            "accepted_structure_similarity": {
                "count": 2,
                "avg": 0.92,
                "min": 0.9,
                "max": 0.94,
            },
            "accepted_real_baseline_comparison": expected_real_baseline,
            "accepted_candidate_quality": {
                "count": 1,
                "avg": 0.95,
                "min": 0.95,
                "max": 0.95,
            },
            "accepted_candidate_quality_components": {
                "cross_receipt_grounding": {
                    "count": 1,
                    "avg": 1.0,
                    "min": 1.0,
                    "max": 1.0,
                }
            },
        },
        {
            "merchant_name": "Market Mart",
            "candidate_count": 2,
            "accepted_count": 1,
            "rejected_count": 1,
            "rejection_reasons": {"merchant_operation_synthetic_cap": 1},
            "accepted_operation_counts": {"add_line_item": 1},
            "accepted_category_counts": {"PRODUCE": 1},
            "accepted_grounded_candidate_count": 1,
            "accepted_arithmetic_candidate_count": 1,
            "accepted_structure_similarity": {
                "count": 1,
                "avg": 0.86,
                "min": 0.86,
                "max": 0.86,
            },
        },
        {
            "merchant_name": "Thin Merchant",
            "candidate_count": 1,
            "accepted_count": 0,
            "rejected_count": 1,
            "rejection_reasons": {"merchant_synthesis_not_ready": 1},
            "accepted_operation_counts": {},
            "accepted_category_counts": {},
            "accepted_grounded_candidate_count": 0,
            "accepted_arithmetic_candidate_count": 0,
            "accepted_structure_similarity": {"count": 0},
        },
    ]
    assert summary["candidate_examples"][0]["candidate_id"] == "sprouts-add"
    assert summary["candidate_examples"][0]["candidate_quality"] == {
        "score": 0.95,
        "high_fidelity": True,
        "components": {
            "arithmetic_reconciliation": 0.9,
            "category_alignment": 1.0,
            "cross_receipt_grounding": 1.0,
            "layout_integrity": 1.0,
            "structure_similarity": 0.94,
            "token_budget": 1.0,
        },
    }
    expected_selection_evidence = {
        "schema_version": "synthetic-candidate-selection-v1",
        "selected_from_candidate_count": 4,
        "selected_input_index": 2,
        "ranked_by": [
            "candidate_quality.high_fidelity",
            "real_baseline_comparison.within_real_score_range",
            "candidate_quality.score",
        ],
        "selected_score": {
            "candidate_quality": 0.95,
            "high_fidelity": True,
            "structure_similarity": 0.94,
            "structure_component_pass_rate": 1.0,
            "layout_integrity": 1.0,
            "token_budget": 1.0,
            "within_real_score_range": True,
            "delta_from_min": 0.12,
            "baseline_pair_count": 66,
            "token_count": 9,
        },
        "selection_policy": (
            "Generate feasible merchant-local mutations, then keep the highest "
            "fidelity option instead of maximizing synthetic volume."
        ),
    }
    assert summary["candidate_examples"][0]["selection_evidence"] == (
        expected_selection_evidence
    )
    assert summary["candidate_examples"][0]["receipt_preview"] == {
        "coordinate_system": "normalized_receipt_0_1000_y_high_is_top",
        "line_count": 5,
        "token_count": 9,
        "truncated": False,
        "text": "SPROUTS PRODUCE YELLOW BANANAS 1.95 BALANCE DUE",
        "lines": [
            {
                "line_number": 3,
                "text": "YELLOW BANANAS 1.95",
                "role": "line_item",
                "synthetic_insert": True,
                "modified_labels": [],
            }
        ],
    }
    assert summary["candidate_examples"][0]["accuracy_evidence"] == {
        "operation": "add_line_item",
        "checks": [
            "item_seen_in_other_receipt",
            "base_receipt_has_category",
            "non_taxable_arithmetic_reconciled",
        ],
        "changed_text": "YELLOW BANANAS",
        "category": "PRODUCE",
        "tax_delta": "0.00",
        "layout_integrity": {
            "score": 1.0,
            "passed": True,
            "line_count": 5,
            "word_count": 9,
            "overlap_pair_count": 0,
            "out_of_bounds_word_count": 0,
            "invalid_word_box_count": 0,
            "line_order_valid": True,
        },
        "structure_similarity": {
            "score": 0.94,
            "nearest_real_receipt_key": "receipt-b#00001",
            "components": {
                "price_column": 0.99,
                "line_step": 0.95,
                "token_count": 0.88,
            },
            "shape_deltas": {
                "line_count_delta": 1.0,
                "line_item_count_delta": 1.0,
                "token_count_delta": 3.0,
                "line_total_x_delta": 0.0,
            },
            "match_summary": {
                "matched_components": [
                    "price_column",
                    "line_step",
                    "token_count",
                ],
                "weak_components": [],
                "shape_checks": [
                    "price_column_aligned",
                    "line_spacing_close",
                    "token_count_close",
                ],
            },
            "real_baseline_comparison": {
                "baseline_receipt_count": 12,
                "baseline_pair_count": 66,
                "candidate_score": 0.94,
                "baseline_avg": 0.91,
                "baseline_min": 0.82,
                "baseline_max": 0.98,
                "within_real_score_range": True,
                "delta_from_avg": 0.03,
                "delta_from_min": 0.12,
            },
        },
        "catalog_grounding": {
            "product_observed_count": 2,
            "product_seen_receipt_count": 2,
            "product_seen_outside_base_count": 1,
            "product_seen_outside_base": ["receipt-b#00001"],
            "category": "PRODUCE",
            "category_seen_count": 4,
            "category_heading_seen_count": 3,
            "category_seen_in_receipts": [
                "receipt-a#00001",
                "receipt-b#00001",
            ],
        },
        "category_placement": {
            "category": "PRODUCE",
            "insert_y": 665.5,
            "shifted_lower_lines_by": 42,
            "selection_reason": (
                "observed in another Sprouts receipt with the same category"
            ),
            "base_receipt_has_category": True,
            "category_seen_count": 4,
            "category_heading_seen_count": 3,
            "category_alignment": "same_category_as_base",
        },
    }
    assert summary["quality_report"]["ready"] is True
    assert summary["quality_report"]["summary"]["acceptance_rate"] == 0.6
    assert summary["quality_report"]["summary"]["ready_contract_count"] == 2
    assert summary["quality_report"]["summary"]["accepted_candidate_quality"] == {
        "count": 2,
        "avg": 0.94,
        "min": 0.93,
        "max": 0.95,
    }
    assert (
        summary["quality_report"]["summary"]["accepted_real_baseline_comparison"]
        == expected_real_baseline
    )
    assert summary["quality_report"]["summary"]["source_quality_status_counts"] == {
        "usable": 2,
        "blocked": 1,
    }
    assert (
        summary["quality_report"]["summary"]["blocked_source_quality_merchant_count"]
        == 1
    )
    assert summary["quality_report"]["summary"]["llm_execution"] == {
        "mode_counts": {"deterministic_fallback": 3},
        "paid_llm_disabled_count": 3,
        "api_call_allowed_count": 0,
        "configured_models": ["openai/gpt-5.5"],
        "latest_model_sources": [
            "https://developers.openai.com/api/docs/guides/latest-model"
        ],
        "latest_model_verified_at": "2026-06-23",
    }
    assert summary["quality_report"]["operation_coverage"]["ready_operation_count"] == 3
    assert summary["quality_report"]["operation_coverage"]["operation_count"] == 4
    assert (
        summary["quality_report"]["operation_coverage"]["operations"]["add_line_item"][
            "ready_merchant_count"
        ]
        == 2
    )
    assert (
        summary["quality_report"]["operation_coverage"]["operations"]["replace_field"][
            "ready_merchant_count"
        ]
        == 1
    )
    assert summary["quality_report"]["accepted_operation_coverage"] == {
        "operation_count": 4,
        "ready_operation_count": 3,
        "accepted_operation_count": 2,
        "accepted_ready_operation_count": 2,
        "accepted_ready_operation_share": 0.667,
        "uncovered_ready_operations": ["replace_field"],
        "operations": {
            "hard_negative": {
                "ready_merchant_count": 1,
                "accepted_merchant_count": 1,
                "accepted_ready_merchant_count": 1,
                "accepted_count": 1,
                "ready_acceptance_share": 1.0,
                "ready_merchants": ["Sprouts Farmers Market"],
                "accepted_merchants": ["Sprouts Farmers Market"],
                "uncovered_ready_merchants": [],
            },
            "add_line_item": {
                "ready_merchant_count": 2,
                "accepted_merchant_count": 2,
                "accepted_ready_merchant_count": 2,
                "accepted_count": 2,
                "ready_acceptance_share": 1.0,
                "ready_merchants": ["Market Mart", "Sprouts Farmers Market"],
                "accepted_merchants": ["Market Mart", "Sprouts Farmers Market"],
                "uncovered_ready_merchants": [],
            },
            "remove_line_item": {
                "ready_merchant_count": 0,
                "accepted_merchant_count": 0,
                "accepted_ready_merchant_count": 0,
                "accepted_count": 0,
                "ready_acceptance_share": None,
                "ready_merchants": [],
                "accepted_merchants": [],
                "uncovered_ready_merchants": [],
            },
            "replace_field": {
                "ready_merchant_count": 1,
                "accepted_merchant_count": 0,
                "accepted_ready_merchant_count": 0,
                "accepted_count": 0,
                "ready_acceptance_share": 0.0,
                "ready_merchants": ["Sprouts Farmers Market"],
                "accepted_merchants": [],
                "uncovered_ready_merchants": ["Sprouts Farmers Market"],
            },
        },
        "recommendations": ["cover_ready_operations_before_training"],
    }
    assert summary["quality_report"]["merchant_gap_summary"] == {
        "blocked_merchant_count": 1,
        "merchant_gap_count": 2,
        "top_blockers": {"no_line_items": 1},
        "top_limitations": {"no_mutable_fields": 1},
        "merchants": [
            {
                "merchant_name": "Market Mart",
                "status": "ready",
                "score": 0.86,
                "candidate_count": 2,
                "accepted_count": 1,
                "ready_operation_count": 1,
                "missing_operations": [],
                "operation_gap_reasons": {},
                "blockers": [],
                "limitations": ["no_mutable_fields"],
            },
            {
                "merchant_name": "Sprouts Farmers Market",
                "status": "ready",
                "score": 0.91,
                "candidate_count": 2,
                "accepted_count": 2,
                "ready_operation_count": 3,
                "missing_operations": [],
                "operation_gap_reasons": {},
                "blockers": [],
                "limitations": [],
            },
            {
                "merchant_name": "Thin Merchant",
                "status": "blocked",
                "score": 0.2,
                "candidate_count": 1,
                "accepted_count": 0,
                "ready_operation_count": 0,
                "missing_operations": [],
                "operation_gap_reasons": {},
                "blockers": ["no_line_items"],
                "limitations": [],
            },
        ],
    }
    assert summary["quality_report"]["quality_gates"] == {
        "validation_policy": "real_receipts_only",
        "train_only_examples": True,
        "contract_gate": {},
        "max_per_merchant": 5,
        "max_per_merchant_operation": 2,
        "min_structure_similarity": 0.6,
        "structure_component_thresholds": {
            "price_column": 0.75,
            "line_step": 0.45,
            "category_sequence": 0.4,
            "category_set": 0.4,
            "token_count": 0.35,
        },
    }
    assert summary["quality_report"]["recommendations"] == [
        "collect_more_receipts_for_not_ready_merchants",
        "fix_source_receipt_quality_before_synthesis",
        "verify_total_and_tax_reconciliation_in_preview",
        "prefer_cross_receipt_grounded_item_mutations",
        "cover_ready_operations_before_training",
    ]
    assert summary["quality_report"]["merchants"][0] == {
        "merchant_name": "Market Mart",
        "readiness_status": "ready",
        "readiness_score": 0.86,
        "source_receipt_count": 4,
        "source_quality_status": "usable",
        "source_quality_receipt_count": 4,
        "source_quality_labeled_word_count": 56,
        "source_quality_receipts_with_line_item_labels": 4,
        "source_quality_receipts_with_grand_total_label": 4,
        "source_quality_receipts_with_date_or_time_label": 3,
        "candidate_count": 2,
        "accepted_count": 1,
        "rejected_count": 1,
        "acceptance_rate": 0.5,
        "supported_operations": ["add_line_item"],
        "contract_ready_operations": ["add_line_item"],
        "accepted_operation_counts": {"add_line_item": 1},
        "accepted_category_counts": {"PRODUCE": 1},
        "accepted_field_replacement_counts": {},
        "accepted_structure_similarity": {
            "count": 1,
            "avg": 0.86,
            "min": 0.86,
            "max": 0.86,
        },
        "rejection_reasons": {"merchant_operation_synthetic_cap": 1},
        "limitations": ["no_mutable_fields"],
    }
    assert summary["quality_report"]["merchants"][1]["merchant_name"] == (
        "Sprouts Farmers Market"
    )
    assert summary["quality_report"]["merchants"][1]["accepted_examples"][0] == {
        "candidate_id": "sprouts-add",
        "operation": "add_line_item",
        "category": "PRODUCE",
        "changed_text": "YELLOW BANANAS",
        "structure_similarity": 0.94,
        "candidate_quality": {
            "score": 0.95,
            "high_fidelity": True,
            "components": {
                "arithmetic_reconciliation": 0.9,
                "category_alignment": 1.0,
                "cross_receipt_grounding": 1.0,
                "layout_integrity": 1.0,
                "structure_similarity": 0.94,
                "token_budget": 1.0,
            },
        },
        "selection_evidence": expected_selection_evidence,
        "accuracy_checks": [
            "item_seen_in_other_receipt",
            "base_receipt_has_category",
            "non_taxable_arithmetic_reconciled",
        ],
        "layout_integrity": {
            "score": 1.0,
            "passed": True,
            "line_count": 5,
            "word_count": 9,
            "overlap_pair_count": 0,
            "out_of_bounds_word_count": 0,
            "invalid_word_box_count": 0,
            "line_order_valid": True,
        },
        "structure_evidence": {
            "score": 0.94,
            "nearest_real_receipt_key": "receipt-b#00001",
            "components": {
                "price_column": 0.99,
                "line_step": 0.95,
                "token_count": 0.88,
            },
            "shape_deltas": {
                "line_count_delta": 1.0,
                "line_item_count_delta": 1.0,
                "token_count_delta": 3.0,
                "line_total_x_delta": 0.0,
            },
            "match_summary": {
                "matched_components": [
                    "price_column",
                    "line_step",
                    "token_count",
                ],
                "weak_components": [],
                "shape_checks": [
                    "price_column_aligned",
                    "line_spacing_close",
                    "token_count_close",
                ],
            },
            "real_baseline_comparison": {
                "baseline_receipt_count": 12,
                "baseline_pair_count": 66,
                "candidate_score": 0.94,
                "baseline_avg": 0.91,
                "baseline_min": 0.82,
                "baseline_max": 0.98,
                "within_real_score_range": True,
                "delta_from_avg": 0.03,
                "delta_from_min": 0.12,
            },
        },
        "catalog_grounding": {
            "product_observed_count": 2,
            "product_seen_receipt_count": 2,
            "product_seen_outside_base_count": 1,
            "product_seen_outside_base": ["receipt-b#00001"],
            "category": "PRODUCE",
            "category_seen_count": 4,
            "category_heading_seen_count": 3,
            "category_seen_in_receipts": [
                "receipt-a#00001",
                "receipt-b#00001",
            ],
        },
        "category_placement": {
            "category": "PRODUCE",
            "insert_y": 665.5,
            "shifted_lower_lines_by": 42,
            "selection_reason": (
                "observed in another Sprouts receipt with the same category"
            ),
            "base_receipt_has_category": True,
            "category_seen_count": 4,
            "category_heading_seen_count": 3,
            "category_alignment": "same_category_as_base",
        },
        "receipt_shape": {
            "line_count": 5,
            "token_count": 9,
            "truncated": False,
        },
        "preview_lines": [
            {
                "line_number": 3,
                "text": "YELLOW BANANAS 1.95",
                "role": "line_item",
                "synthetic_insert": True,
                "modified_labels": [],
            }
        ],
    }
    assert summary["quality_report"]["merchants"][2]["merchant_name"] == (
        "Thin Merchant"
    )
    assert summary["quality_report"]["merchants"][2]["readiness_status"] == "blocked"
    assert summary["quality_report"]["merchants"][2]["source_quality_status"] == (
        "blocked"
    )
    assert summary["quality_report"]["merchants"][2][
        "source_quality_operation_blockers"
    ] == {
        "hard_negative": "source_receipt_quality_blocked",
        "add_line_item": "source_receipt_quality_blocked",
        "remove_line_item": "source_receipt_quality_blocked",
        "replace_field": "source_receipt_quality_blocked",
    }
    assert "tokens" not in summary["candidate_examples"][0]


def test_summarize_synthesis_bundle_counts_accepted_field_replacements(
    monkeypatch,
):
    module = _load_module(monkeypatch)

    bundle = {
        "schema_version": "layoutlm-synthetic-training-bundle-v1",
        "candidate_mix": {
            "candidate_count": 1,
            "accepted_count": 1,
            "accepted_operation_counts": {"replace_field": 1},
            "accepted_field_replacement_counts": {"DATE": 2},
            "operation_counts": {"replace_field": 1},
        },
        "synthetic_training_examples": [
            {
                "candidate_id": "sprouts-date",
                "merchant_name": "Sprouts Farmers Market",
                "metadata": {
                    "source": "merchant_mutable_field_geometry",
                    "operation": "replace_field",
                    "field_replacement": {
                        "label": "DATE",
                        "old_text": "05/13/2026",
                        "new_text": "05/14/2026",
                        "format": "MM/DD/YYYY",
                    },
                    "mutable_field_evidence": {
                        "safe_to_mutate": True,
                        "observed_count": 4,
                        "stable_format": "MM/DD/YYYY",
                        "stable_geometry": True,
                    },
                },
                "tokens": ["05/14/2026"],
            }
        ],
    }

    summary = module._summarize_synthesis_artifacts(
        [bundle],
        artifact_ref={"s3_uri": "s3://pattern-bucket/bundles/run-1.json"},
        synthetic_train_examples=1,
    )

    assert summary["accepted_operation_counts"] == {"replace_field": 1}
    assert summary["accepted_field_replacement_counts"] == {"DATE": 2}
    assert summary["candidate_examples"][0]["field_label"] == "DATE"
    assert summary["candidate_examples"][0]["old_text"] == "05/13/2026"
    assert summary["candidate_examples"][0]["new_text"] == "05/14/2026"
    assert "tokens" not in summary["candidate_examples"][0]


def test_build_synthesis_summary_includes_loader_quality_metrics(monkeypatch):
    module = _load_module(monkeypatch)

    summary = module._build_synthesis_summary(
        SimpleNamespace(tags={}, job_config={}),
        {
            "synthetic_train_examples": 2,
            "synthetic_candidates_seen": 5,
            "synthetic_candidates_accepted": 2,
            "synthetic_candidates_rejected": 3,
            "synthetic_rejection_reasons": {
                "low_structure_similarity": 2,
                "not_train_only": 1,
                "zero": 0,
            },
            "synthetic_accepted_operation_counts": {
                "add_line_item": 1,
                "replace_field": 1,
            },
            "synthetic_accepted_operation_coverage": {
                "operation_count": 4,
                "ready_operation_count": 2,
                "accepted_operation_count": 1,
                "accepted_ready_operation_count": 1,
                "accepted_ready_operation_share": 0.5,
                "uncovered_ready_operations": ["add_line_item"],
                "operations": {
                    "add_line_item": {
                        "ready_merchant_count": 1,
                        "accepted_merchant_count": 0,
                        "accepted_ready_merchant_count": 0,
                        "accepted_count": 0,
                        "ready_acceptance_share": 0.0,
                        "ready_merchants": ["Sprouts Farmers Market"],
                        "accepted_merchants": [],
                        "uncovered_ready_merchants": ["Sprouts Farmers Market"],
                    },
                    "replace_field": {
                        "ready_merchant_count": 1,
                        "accepted_merchant_count": 1,
                        "accepted_ready_merchant_count": 1,
                        "accepted_count": 1,
                        "ready_acceptance_share": 1.0,
                        "ready_merchants": ["Sprouts Farmers Market"],
                        "accepted_merchants": ["Sprouts Farmers Market"],
                        "uncovered_ready_merchants": [],
                    },
                },
                "recommendations": ["cover_ready_operations_before_training"],
            },
            "synthetic_accepted_category_counts": {"PRODUCE": 1},
            "synthetic_accepted_field_replacement_counts": {"DATE": 1},
            "synthetic_accepted_structure_similarity": {
                "count": 2,
                "avg": 0.915,
                "min": 0.9,
                "max": 0.93,
            },
            "synthetic_accepted_structure_components": {
                "price_column": {
                    "count": 2,
                    "avg": 1.0,
                    "min": 1.0,
                    "max": 1.0,
                },
                "line_step": {
                    "count": 2,
                    "avg": 0.6,
                    "min": 0.55,
                    "max": 0.65,
                },
            },
            "synthetic_accepted_candidate_quality": {
                "count": 2,
                "avg": 0.94,
                "min": 0.91,
                "max": 0.97,
            },
            "synthetic_accepted_candidate_quality_components": {
                "cross_receipt_grounding": {
                    "count": 1,
                    "avg": 1.0,
                    "min": 1.0,
                    "max": 1.0,
                },
                "stable_field_geometry": {
                    "count": 1,
                    "avg": 1.0,
                    "min": 1.0,
                    "max": 1.0,
                },
            },
            "synthetic_accepted_real_baseline_comparison": {
                "count": 2,
                "within_real_score_range_count": 2,
                "below_real_score_range_count": 0,
                "within_real_score_range_share": 1.0,
                "candidate_score": {
                    "count": 2,
                    "avg": 0.915,
                    "min": 0.9,
                    "max": 0.93,
                },
                "baseline_avg": {
                    "count": 2,
                    "avg": 0.89,
                    "min": 0.88,
                    "max": 0.9,
                },
                "baseline_min": {
                    "count": 2,
                    "avg": 0.8,
                    "min": 0.8,
                    "max": 0.8,
                },
                "baseline_pair_count": {
                    "count": 2,
                    "avg": 66,
                    "min": 66,
                    "max": 66,
                },
                "delta_from_avg": {
                    "count": 2,
                    "avg": 0.025,
                    "min": 0.02,
                    "max": 0.03,
                },
                "delta_from_min": {
                    "count": 2,
                    "avg": 0.115,
                    "min": 0.1,
                    "max": 0.13,
                },
            },
            "synthetic_accepted_mix_balance": {
                "accepted_count": 2,
                "merchant_count": 2,
                "operation_count": 2,
                "top_merchant": "Sprouts Farmers Market",
                "top_merchant_count": 1,
                "top_merchant_share": 0.5,
                "top_operation": "replace_field",
                "top_operation_count": 1,
                "top_operation_share": 0.5,
                "merchant_entropy": 1.0,
                "operation_entropy": 1.0,
                "risk_level": "low",
                "risk_reasons": ["too_few_examples_for_balance_assessment"],
            },
            "synthetic_accepted_grounded_count": 1,
            "synthetic_accepted_arithmetic_count": 1,
        },
    )

    assert summary == {
        "status": "metrics_only",
        "synthetic_train_examples": 2,
        "validation_policy": "real_receipts_only",
        "synthetic_candidates_seen": 5,
        "synthetic_candidates_accepted": 2,
        "synthetic_candidates_rejected": 3,
        "synthetic_rejection_reasons": {
            "low_structure_similarity": 2,
            "not_train_only": 1,
        },
        "synthetic_accepted_operation_counts": {
            "add_line_item": 1,
            "replace_field": 1,
        },
        "accepted_operation_counts": {
            "add_line_item": 1,
            "replace_field": 1,
        },
        "synthetic_accepted_operation_coverage": {
            "operation_count": 4,
            "ready_operation_count": 2,
            "accepted_operation_count": 1,
            "accepted_ready_operation_count": 1,
            "accepted_ready_operation_share": 0.5,
            "uncovered_ready_operations": ["add_line_item"],
            "operations": {
                "add_line_item": {
                    "ready_merchant_count": 1,
                    "accepted_merchant_count": 0,
                    "accepted_ready_merchant_count": 0,
                    "accepted_count": 0,
                    "ready_acceptance_share": 0.0,
                    "ready_merchants": ["Sprouts Farmers Market"],
                    "accepted_merchants": [],
                    "uncovered_ready_merchants": ["Sprouts Farmers Market"],
                },
                "replace_field": {
                    "ready_merchant_count": 1,
                    "accepted_merchant_count": 1,
                    "accepted_ready_merchant_count": 1,
                    "accepted_count": 1,
                    "ready_acceptance_share": 1.0,
                    "ready_merchants": ["Sprouts Farmers Market"],
                    "accepted_merchants": ["Sprouts Farmers Market"],
                    "uncovered_ready_merchants": [],
                },
            },
            "recommendations": ["cover_ready_operations_before_training"],
        },
        "accepted_operation_coverage": {
            "operation_count": 4,
            "ready_operation_count": 2,
            "accepted_operation_count": 1,
            "accepted_ready_operation_count": 1,
            "accepted_ready_operation_share": 0.5,
            "uncovered_ready_operations": ["add_line_item"],
            "operations": {
                "add_line_item": {
                    "ready_merchant_count": 1,
                    "accepted_merchant_count": 0,
                    "accepted_ready_merchant_count": 0,
                    "accepted_count": 0,
                    "ready_acceptance_share": 0.0,
                    "ready_merchants": ["Sprouts Farmers Market"],
                    "accepted_merchants": [],
                    "uncovered_ready_merchants": ["Sprouts Farmers Market"],
                },
                "replace_field": {
                    "ready_merchant_count": 1,
                    "accepted_merchant_count": 1,
                    "accepted_ready_merchant_count": 1,
                    "accepted_count": 1,
                    "ready_acceptance_share": 1.0,
                    "ready_merchants": ["Sprouts Farmers Market"],
                    "accepted_merchants": ["Sprouts Farmers Market"],
                    "uncovered_ready_merchants": [],
                },
            },
            "recommendations": ["cover_ready_operations_before_training"],
        },
        "synthetic_accepted_category_counts": {"PRODUCE": 1},
        "accepted_category_counts": {"PRODUCE": 1},
        "synthetic_accepted_field_replacement_counts": {"DATE": 1},
        "accepted_field_replacement_counts": {"DATE": 1},
        "synthetic_accepted_structure_similarity": {
            "count": 2,
            "avg": 0.915,
            "min": 0.9,
            "max": 0.93,
        },
        "accepted_structure_similarity": {
            "count": 2,
            "avg": 0.915,
            "min": 0.9,
            "max": 0.93,
        },
        "synthetic_accepted_structure_components": {
            "price_column": {
                "count": 2,
                "avg": 1.0,
                "min": 1.0,
                "max": 1.0,
            },
            "line_step": {
                "count": 2,
                "avg": 0.6,
                "min": 0.55,
                "max": 0.65,
            },
        },
        "accepted_structure_components": {
            "price_column": {
                "count": 2,
                "avg": 1.0,
                "min": 1.0,
                "max": 1.0,
            },
            "line_step": {
                "count": 2,
                "avg": 0.6,
                "min": 0.55,
                "max": 0.65,
            },
        },
        "synthetic_accepted_candidate_quality": {
            "count": 2,
            "avg": 0.94,
            "min": 0.91,
            "max": 0.97,
        },
        "accepted_candidate_quality": {
            "count": 2,
            "avg": 0.94,
            "min": 0.91,
            "max": 0.97,
        },
        "synthetic_accepted_candidate_quality_components": {
            "cross_receipt_grounding": {
                "count": 1,
                "avg": 1.0,
                "min": 1.0,
                "max": 1.0,
            },
            "stable_field_geometry": {
                "count": 1,
                "avg": 1.0,
                "min": 1.0,
                "max": 1.0,
            },
        },
        "accepted_candidate_quality_components": {
            "cross_receipt_grounding": {
                "count": 1,
                "avg": 1.0,
                "min": 1.0,
                "max": 1.0,
            },
            "stable_field_geometry": {
                "count": 1,
                "avg": 1.0,
                "min": 1.0,
                "max": 1.0,
            },
        },
        "synthetic_accepted_real_baseline_comparison": {
            "count": 2,
            "within_real_score_range_count": 2,
            "below_real_score_range_count": 0,
            "within_real_score_range_share": 1.0,
            "candidate_score": {
                "count": 2,
                "avg": 0.915,
                "min": 0.9,
                "max": 0.93,
            },
            "baseline_avg": {
                "count": 2,
                "avg": 0.89,
                "min": 0.88,
                "max": 0.9,
            },
            "baseline_min": {
                "count": 2,
                "avg": 0.8,
                "min": 0.8,
                "max": 0.8,
            },
            "baseline_pair_count": {
                "count": 2,
                "avg": 66.0,
                "min": 66.0,
                "max": 66.0,
            },
            "delta_from_avg": {
                "count": 2,
                "avg": 0.025,
                "min": 0.02,
                "max": 0.03,
            },
            "delta_from_min": {
                "count": 2,
                "avg": 0.115,
                "min": 0.1,
                "max": 0.13,
            },
        },
        "accepted_real_baseline_comparison": {
            "count": 2,
            "within_real_score_range_count": 2,
            "below_real_score_range_count": 0,
            "within_real_score_range_share": 1.0,
            "candidate_score": {
                "count": 2,
                "avg": 0.915,
                "min": 0.9,
                "max": 0.93,
            },
            "baseline_avg": {
                "count": 2,
                "avg": 0.89,
                "min": 0.88,
                "max": 0.9,
            },
            "baseline_min": {
                "count": 2,
                "avg": 0.8,
                "min": 0.8,
                "max": 0.8,
            },
            "baseline_pair_count": {
                "count": 2,
                "avg": 66.0,
                "min": 66.0,
                "max": 66.0,
            },
            "delta_from_avg": {
                "count": 2,
                "avg": 0.025,
                "min": 0.02,
                "max": 0.03,
            },
            "delta_from_min": {
                "count": 2,
                "avg": 0.115,
                "min": 0.1,
                "max": 0.13,
            },
        },
        "synthetic_accepted_mix_balance": {
            "accepted_count": 2,
            "merchant_count": 2,
            "operation_count": 2,
            "top_merchant": "Sprouts Farmers Market",
            "top_merchant_count": 1,
            "top_merchant_share": 0.5,
            "top_operation": "replace_field",
            "top_operation_count": 1,
            "top_operation_share": 0.5,
            "merchant_entropy": 1.0,
            "operation_entropy": 1.0,
            "risk_level": "low",
            "risk_reasons": ["too_few_examples_for_balance_assessment"],
        },
        "accepted_mix_balance": {
            "accepted_count": 2,
            "merchant_count": 2,
            "operation_count": 2,
            "top_merchant": "Sprouts Farmers Market",
            "top_merchant_count": 1,
            "top_merchant_share": 0.5,
            "top_operation": "replace_field",
            "top_operation_count": 1,
            "top_operation_share": 0.5,
            "merchant_entropy": 1.0,
            "operation_entropy": 1.0,
            "risk_level": "low",
            "risk_reasons": ["too_few_examples_for_balance_assessment"],
        },
        "synthetic_accepted_grounded_count": 1,
        "accepted_grounded_candidate_count": 1,
        "grounded_candidate_count": 1,
        "synthetic_accepted_arithmetic_count": 1,
        "accepted_arithmetic_candidate_count": 1,
        "arithmetic_candidate_count": 1,
    }


def test_build_synthesis_summary_falls_back_to_metrics_only(monkeypatch):
    module = _load_module(monkeypatch)

    summary = module._build_synthesis_summary(
        SimpleNamespace(tags={}, job_config={}),
        {"synthetic_train_examples": 9},
    )

    assert summary == {
        "status": "metrics_only",
        "synthetic_train_examples": 9,
        "validation_policy": "real_receipts_only",
    }
