"""Tests for train-only synthetic LayoutLM example loading."""

import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from receipt_layoutlm.data_loader import (
    _load_synthetic_training_examples,
    _load_synthetic_training_examples_with_summary,
)


def _candidate(
    train_only=True,
    *,
    candidate_id="candidate-1",
    merchant_name="Sprouts Farmers Market",
    structure_score=0.93,
    baseline_min=0.80,
    baseline_within=True,
    baseline_pair_count=66,
):
    return {
        "candidate_id": candidate_id,
        "merchant_name": merchant_name,
        "image_id": f"synthetic-{candidate_id}",
        "receipt_key": f"synthetic-{candidate_id}#00001",
        "train_only": train_only,
        "tokens": ["SPROUTS", "REWARDS", "4.49"],
        "bboxes": [
            [390, 60, 470, 88],
            [390, 80, 480, 108],
            [720, 520, 780, 548],
        ],
        "ner_tags": ["B-MERCHANT_NAME", "O", "B-LINE_TOTAL"],
        "metadata": {
            "source": "sprouts_parameterized_geometry",
            "operation": "hard_negative",
            "base_receipt_key": "base#00001",
            "actual_label": "O",
            "predicted_label": "LINE_TOTAL",
            "error_kind": "false_positive",
            "structure_similarity": {
                "score": structure_score,
                "nearest_real_receipt_key": "real#00001",
                "components": {
                    "category_sequence": 0.67,
                    "category_set": 0.50,
                    "item_count": 0.50,
                    "token_count": 0.47,
                    "price_column": 1.00,
                    "line_step": 0.55,
                },
                "real_baseline_comparison": {
                    "baseline_receipt_count": 12,
                    "baseline_pair_count": baseline_pair_count,
                    "candidate_score": structure_score,
                    "baseline_avg": 0.90,
                    "baseline_min": baseline_min,
                    "baseline_max": 0.98,
                    "within_real_score_range": baseline_within,
                    "delta_from_avg": round(structure_score - 0.90, 3),
                    "delta_from_min": round(structure_score - baseline_min, 3),
                },
            },
        },
    }


def _grounded_add_item_metadata():
    return {
        "source": "sprouts_arithmetic_geometry",
        "operation": "add_line_item",
        "base_receipt_key": "base#00001",
        "added_item": {
            "product_text": "YELLOW BANANAS",
            "category": "PRODUCE",
            "line_total": "1.95",
            "seen_in_other_receipt": True,
        },
        "observed_item_evidence": {
            "product_seen_outside_base": ["source#00001"],
            "category": "PRODUCE",
            "category_seen_in_receipts": ["base#00001", "source#00001"],
            "category_seen_count": 2,
            "base_receipt_has_category": True,
        },
        "arithmetic_reconciliation": {
            "summary_update_policy": "non_taxable_item_delta",
            "tax_delta": "0.00",
        },
        "structure_similarity": {
            "score": 0.90,
            "components": {
                "category_sequence": 0.67,
                "category_set": 0.50,
                "item_count": 0.33,
                "token_count": 0.47,
                "price_column": 1.00,
                "line_step": 0.65,
            },
            "real_baseline_comparison": {
                "baseline_receipt_count": 12,
                "baseline_pair_count": 66,
                "candidate_score": 0.90,
                "baseline_avg": 0.88,
                "baseline_min": 0.80,
                "baseline_max": 0.98,
                "within_real_score_range": True,
                "delta_from_avg": 0.02,
                "delta_from_min": 0.10,
            },
        },
        "layout_integrity": {
            "score": 1.0,
            "edit_introduced_overlap_pair_count": 0,
            "invalid_word_box_count": 0,
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
            "catalog_grounding": {
                "product_observed_count": 2,
                "product_seen_receipt_count": 2,
                "product_seen_outside_base_count": 1,
                "product_seen_outside_base": ["source#00001"],
                "category": "PRODUCE",
                "category_seen_count": 2,
            },
            "category_placement": {
                "category": "PRODUCE",
                "base_receipt_has_category": True,
                "category_seen_count": 2,
                "category_alignment": "same_category_as_base",
            },
        },
    }


def _replace_field_candidate():
    candidate = _candidate(candidate_id="replace-date")
    candidate["tokens"] = ["MARKET", "05/14/2026", "15:24", "8.00"]
    candidate["bboxes"] = [
        [410, 950, 500, 975],
        [105, 890, 220, 915],
        [245, 890, 320, 915],
        [830, 525, 885, 550],
    ]
    candidate["ner_tags"] = [
        "B-MERCHANT_NAME",
        "B-DATE",
        "B-TIME",
        "B-GRAND_TOTAL",
    ]
    candidate["metadata"] = {
        "source": "merchant_mutable_field_geometry",
        "operation": "replace_field",
        "base_receipt_key": "base#00001",
        "field_replacement": {
            "label": "DATE",
            "old_text": "05/13/2026",
            "new_text": "05/14/2026",
            "format": "MM/DD/YYYY",
        },
        "mutable_field_evidence": {
            "label": "DATE",
            "safe_to_mutate": True,
            "observed_count": 2,
            "examples": ["05/12/2026", "05/13/2026"],
            "format_counts": {"MM/DD/YYYY": 2},
            "stable_format": "MM/DD/YYYY",
            "stable_geometry": True,
            "blockers": [],
        },
        "structure_similarity": {"score": 0.91},
        "layout_integrity": {
            "score": 1.0,
            "edit_introduced_overlap_pair_count": 0,
            "invalid_word_box_count": 0,
        },
    }
    return candidate


def _value_scrub_candidate(
    *,
    label="PAYMENT_METHOD",
    old_text="XXXXXXXXXXXX1454",
    new_text="XXXXXXXXXXXX3618",
    scrub_kind="masked_pan",
    token_count_preserved=True,
):
    """A privacy-safe value-scrub replace_field candidate (digits-only change)."""
    candidate = _candidate(candidate_id="scrub-pan")
    # The token sequence carries the SCRUBBED value (the generator already
    # applied it); the loader verifies the scrub against these tokens, not the
    # metadata's claim.
    candidate["tokens"] = ["MASTERCARD", new_text]
    candidate["bboxes"] = [[400, 520, 520, 548], [540, 520, 770, 548]]
    candidate["ner_tags"] = ["B-PAYMENT_METHOD", "B-PAYMENT_METHOD"]
    candidate["metadata"] = {
        "source": "merchant_value_scrub_geometry",
        "operation": "replace_field",
        "base_receipt_key": "base#00001",
        "field_replacement": {
            "label": label,
            "old_text": old_text,
            "new_text": new_text,
            "format": "value_scrub",
        },
        "mutable_field_evidence": {
            "label": label,
            "safe_to_mutate": True,
            "mutation_kind": "value_scrub",
            "scrub_kind": scrub_kind,
            "stable_format": "value_scrub",
            "stable_geometry": True,
            "token_count_preserved": token_count_preserved,
            "format_preserved": True,
            "observed_count": 1,
            "examples": [old_text],
        },
        "structure_similarity": {"score": 0.9},
        "layout_integrity": {"score": 1.0},
    }
    return candidate


def test_load_synthetic_training_examples_accepts_value_scrub_masked_pan(tmp_path):
    path = tmp_path / "patterns.json"
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [_value_scrub_candidate()]}),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))
    assert loaded.candidates_accepted == 1
    assert loaded.rejection_reasons == {}


def test_load_synthetic_training_examples_rejects_value_scrub_altered_structure(
    tmp_path,
):
    # The scrub changed a mask character (an 'X' became a digit): not digits-only.
    bad = _value_scrub_candidate(
        old_text="XXXXXXXXXXXX1454", new_text="XXXXXXXXXXX91454"
    )
    path = tmp_path / "patterns.json"
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [bad]}), encoding="utf-8"
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))
    assert loaded.candidates_accepted == 0
    assert loaded.rejection_reasons.get("replace_field_scrub_altered_structure") == 1


def test_load_synthetic_training_examples_rejects_value_scrub_unsupported_label(
    tmp_path,
):
    # value_scrub is only allowed for PAYMENT_METHOD / LOYALTY_ID.
    bad = _value_scrub_candidate(label="WEBSITE")
    bad["ner_tags"] = ["B-WEBSITE", "B-WEBSITE"]
    path = tmp_path / "patterns.json"
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [bad]}), encoding="utf-8"
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))
    assert loaded.candidates_accepted == 0
    assert loaded.rejection_reasons.get("replace_field_unsupported_label") == 1


def test_load_synthetic_training_examples_rejects_value_scrub_mask_char_to_digit(
    tmp_path,
):
    # A literal '#' mask char turned into a digit: only digits may change, so a
    # '#'->digit edit must be rejected (the old digit->'#' skeleton missed this).
    bad = _value_scrub_candidate(
        label="LOYALTY_ID",
        old_text="###-###-9416",
        new_text="123-###-9416",
        scrub_kind="separated_id",
    )
    bad["ner_tags"] = ["B-PAYMENT_METHOD", "B-LOYALTY_ID"]
    path = tmp_path / "patterns.json"
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [bad]}), encoding="utf-8"
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))
    assert loaded.candidates_accepted == 0
    assert loaded.rejection_reasons.get("replace_field_scrub_altered_structure") == 1


def test_load_synthetic_training_examples_rejects_value_scrub_not_in_tokens(
    tmp_path,
):
    # Metadata claims a scrub, but the actual token still holds the original
    # (unscrubbed) value — the privacy scrub was never applied to the data.
    bad = _value_scrub_candidate(
        old_text="XXXXXXXXXXXX1454", new_text="XXXXXXXXXXXX3618"
    )
    bad["tokens"] = ["MASTERCARD", "XXXXXXXXXXXX1454"]  # original, not scrubbed
    path = tmp_path / "patterns.json"
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [bad]}), encoding="utf-8"
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))
    assert loaded.candidates_accepted == 0
    reasons = loaded.rejection_reasons
    assert (
        reasons.get("replace_field_scrub_not_applied")
        or reasons.get("replace_field_scrub_residual_original")
    )


def _merchant_contract(
    *,
    merchant_name="Sprouts Farmers Market",
    status="ready",
    supported_operations=None,
    operation_contracts=None,
):
    return {
        "merchant_name": merchant_name,
        "status": status,
        "supported_operations": supported_operations
        or [
            "hard_negative",
            "add_line_item",
            "remove_line_item",
            "replace_field",
        ],
        "operation_contracts": operation_contracts
        or {
            "hard_negative": {"ready": True},
            "add_line_item": {"ready": True},
            "remove_line_item": {"ready": True},
            "replace_field": {
                "ready": True,
                "fields": {
                    "DATE": {
                        "safe_to_mutate": True,
                        "stable_format": "MM/DD/YYYY",
                        "stable_geometry": True,
                        "observed_count": 2,
                    },
                    "TIME": {
                        "safe_to_mutate": True,
                        "stable_format": "HH:MM",
                        "stable_geometry": True,
                        "observed_count": 2,
                    },
                },
            },
        },
    }


def test_load_synthetic_training_examples_accepts_pattern_artifact(tmp_path):
    path = tmp_path / "patterns.json"
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [_candidate()]}),
        encoding="utf-8",
    )

    examples = _load_synthetic_training_examples(str(path))

    assert examples == [
        {
            "tokens": ["SPROUTS", "REWARDS", "4.49"],
            "bboxes": [
                [390, 60, 470, 88],
                [390, 80, 480, 108],
                [720, 520, 780, 548],
            ],
            "ner_tags": ["B-MERCHANT_NAME", "O", "B-LINE_TOTAL"],
            "receipt_key": "synthetic-candidate-1#00001",
            "image_id": "synthetic-candidate-1",
        }
    ]


def test_load_synthetic_training_examples_rejects_non_training_ready_bundle(
    tmp_path,
):
    path = tmp_path / "bundle.json"
    path.write_text(
        json.dumps(
            {
                "synthesis_quality_report": {
                    "ready": True,
                    "training_ready": False,
                    "training_ready_reasons": [
                        "cover_ready_operations_before_training"
                    ],
                },
                "synthetic_training_examples": [_candidate()],
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.examples == []
    assert loaded.accepted_rows == []
    assert loaded.candidates_seen == 1
    assert loaded.candidates_accepted == 0
    assert loaded.candidates_rejected == 1
    assert loaded.rejection_reasons == {"bundle_training_not_ready": 1}
    assert loaded.rejected_rows == [
        {
            "candidate_id": "candidate-1",
            "receipt_key": "synthetic-candidate-1#00001",
            "image_id": "synthetic-candidate-1",
            "merchant_name": "Sprouts Farmers Market",
            "operation": "hard_negative",
            "reason": "bundle_training_not_ready",
            "idx": 0,
            "structure_similarity": 0.93,
        }
    ]


def test_load_synthetic_training_examples_accepts_training_ready_bundle(
    tmp_path,
):
    path = tmp_path / "bundle.json"
    path.write_text(
        json.dumps(
            {
                "synthesis_quality_report": {
                    "ready": True,
                    "training_ready": True,
                    "training_ready_reasons": [],
                },
                "synthetic_training_examples": [_candidate()],
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 1
    assert loaded.candidates_accepted == 1
    assert loaded.candidates_rejected == 0
    assert loaded.rejection_reasons == {}
    assert [row["candidate_id"] for row in loaded.accepted_rows] == ["candidate-1"]


def test_load_synthetic_training_examples_prefers_training_ready_over_legacy_ready(
    tmp_path,
):
    path = tmp_path / "bundle.json"
    path.write_text(
        json.dumps(
            {
                "ready": False,
                "synthesis_quality_report": {
                    "ready": False,
                    "training_ready": True,
                    "training_ready_reasons": [],
                },
                "synthetic_training_examples": [_candidate()],
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 1
    assert loaded.candidates_accepted == 1
    assert loaded.candidates_rejected == 0
    assert loaded.rejection_reasons == {}


def test_load_synthetic_training_examples_treats_null_training_ready_as_legacy(
    tmp_path,
):
    path = tmp_path / "bundle.json"
    path.write_text(
        json.dumps(
            {
                "synthesis_quality_report": {
                    "ready": False,
                    "training_ready": None,
                    "training_ready_reasons": [],
                },
                "synthetic_training_examples": [_candidate()],
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 1
    assert loaded.candidates_accepted == 0
    assert loaded.candidates_rejected == 1
    assert loaded.rejection_reasons == {"bundle_not_ready": 1}


def test_load_synthetic_training_examples_rejects_only_not_ready_artifact_in_directory(
    tmp_path,
):
    blocked = tmp_path / "blocked.json"
    blocked.write_text(
        json.dumps(
            {
                "synthesis_quality_report": {
                    "ready": True,
                    "training_ready": False,
                    "training_ready_reasons": [
                        "cover_ready_operations_before_training"
                    ],
                },
                "synthetic_training_examples": [_candidate(candidate_id="blocked")],
            }
        ),
        encoding="utf-8",
    )
    allowed = tmp_path / "allowed.json"
    allowed.write_text(
        json.dumps(
            {
                "synthesis_quality_report": {
                    "ready": True,
                    "training_ready": True,
                    "training_ready_reasons": [],
                },
                "synthetic_training_examples": [_candidate(candidate_id="allowed")],
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(tmp_path))

    assert loaded.candidates_seen == 2
    assert loaded.candidates_accepted == 1
    assert loaded.candidates_rejected == 1
    assert loaded.rejection_reasons == {"bundle_training_not_ready": 1}
    assert [row["candidate_id"] for row in loaded.accepted_rows] == ["allowed"]
    assert [row["candidate_id"] for row in loaded.rejected_rows] == ["blocked"]


def test_load_synthetic_training_examples_skips_validation_rows(tmp_path):
    path = tmp_path / "patterns.json"
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [_candidate(train_only=False)]}),
        encoding="utf-8",
    )

    assert _load_synthetic_training_examples(str(path)) == []


def test_load_synthetic_training_examples_rejects_bad_shapes(tmp_path):
    path = tmp_path / "patterns.json"
    bad = _candidate()
    bad["bboxes"] = [[390, 60, 470]]
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [bad]}),
        encoding="utf-8",
    )

    assert _load_synthetic_training_examples(str(path)) == []


def test_load_synthetic_training_examples_rejects_inverted_box(tmp_path):
    """A box with y0 > y1 (or x0 > x1) is corrupted geometry and must be
    rejected even when the row's declared layout_integrity claims it is clean —
    defense-in-depth against rows authored outside the synthesis pipeline."""
    path = tmp_path / "patterns.json"
    inverted = _candidate()
    inverted["metadata"] = _grounded_add_item_metadata()
    # Flip the y coordinates of the first box so y0 > y1.
    inverted["bboxes"][0] = [390, 88, 470, 60]
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [inverted]}),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))
    assert loaded.candidates_accepted == 0
    assert loaded.rejection_reasons.get("invalid_bboxes") == 1


def test_load_synthetic_training_examples_requires_layout_integrity_for_edits(
    tmp_path,
):
    """Geometry-mutating ops must carry a layout_integrity score; a hard_negative
    (which moves no boxes) is exempt and still loads without one."""
    path = tmp_path / "patterns.json"

    add_no_layout = _candidate()
    add_no_layout["candidate_id"] = "add-no-layout"
    add_no_layout["metadata"] = _grounded_add_item_metadata()
    del add_no_layout["metadata"]["layout_integrity"]

    hard_negative_no_layout = _candidate()  # default op is hard_negative
    assert "layout_integrity" not in hard_negative_no_layout["metadata"]

    path.write_text(
        json.dumps(
            {
                "synthetic_receipt_candidates": [
                    add_no_layout,
                    hard_negative_no_layout,
                ]
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))
    assert loaded.rejection_reasons.get("missing_layout_integrity") == 1
    assert loaded.candidates_accepted == 1  # the hard_negative still loads


def test_load_synthetic_training_examples_rejects_low_structure_similarity(
    tmp_path,
):
    path = tmp_path / "patterns.json"
    weak = _candidate()
    weak["metadata"]["structure_similarity"]["score"] = 0.31
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [weak]}),
        encoding="utf-8",
    )

    assert _load_synthetic_training_examples(str(path)) == []


def test_load_synthetic_training_examples_rejects_missing_base_lineage(
    tmp_path,
):
    path = tmp_path / "patterns.json"
    missing_lineage = _candidate()
    del missing_lineage["metadata"]["base_receipt_key"]
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [missing_lineage]}),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 1
    assert loaded.candidates_accepted == 0
    assert loaded.rejection_reasons == {"missing_base_receipt_lineage": 1}
    assert loaded.rejected_rows == [
        {
            "candidate_id": "candidate-1",
            "receipt_key": "synthetic-candidate-1#00001",
            "image_id": "synthetic-candidate-1",
            "merchant_name": "Sprouts Farmers Market",
            "operation": "hard_negative",
            "reason": "missing_base_receipt_lineage",
            "idx": 0,
            "structure_similarity": 0.93,
        }
    ]


def test_load_synthetic_training_examples_rejects_low_declared_candidate_quality(
    tmp_path,
):
    path = tmp_path / "patterns.json"
    weak = _candidate()
    weak["metadata"]["candidate_quality"] = {
        "schema_version": "synthetic-candidate-quality-v1",
        "score": 0.42,
        "high_fidelity": False,
        "components": {
            "structure_similarity": 0.93,
            "target_label_slot": 1.0,
            "local_distractor": 1.0,
        },
    }
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [weak]}),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 1
    assert loaded.candidates_accepted == 0
    assert loaded.rejection_reasons == {"low_candidate_quality": 1}
    assert loaded.rejected_rows == [
        {
            "candidate_id": "candidate-1",
            "receipt_key": "synthetic-candidate-1#00001",
            "image_id": "synthetic-candidate-1",
            "merchant_name": "Sprouts Farmers Market",
            "operation": "hard_negative",
            "reason": "low_candidate_quality",
            "idx": 0,
            "structure_similarity": 0.93,
            "candidate_quality": 0.42,
        }
    ]


def test_load_synthetic_training_examples_rejects_weak_structure_component(
    tmp_path,
):
    path = tmp_path / "patterns.json"
    bad_geometry = _candidate()
    bad_geometry["metadata"] = _grounded_add_item_metadata()
    bad_geometry["metadata"]["structure_similarity"] = {
        "score": 0.91,
        "nearest_real_receipt_key": "real#00001",
        "components": {
            "category_sequence": 0.67,
            "category_set": 0.50,
            "item_count": 0.33,
            "token_count": 0.47,
            "price_column": 0.30,
            "line_step": 0.65,
        },
    }
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [bad_geometry]}),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 1
    assert loaded.candidates_accepted == 0
    assert loaded.candidates_rejected == 1
    assert loaded.rejection_reasons == {"low_price_column_similarity": 1}
    assert loaded.rejected_rows == [
        {
            "candidate_id": "candidate-1",
            "receipt_key": "synthetic-candidate-1#00001",
            "image_id": "synthetic-candidate-1",
            "merchant_name": "Sprouts Farmers Market",
            "operation": "add_line_item",
            "reason": "low_price_column_similarity",
            "idx": 0,
            "category": "PRODUCE",
            "structure_similarity": 0.91,
        }
    ]


def test_load_synthetic_training_examples_rejects_below_real_structure_baseline(
    tmp_path,
):
    path = tmp_path / "patterns.json"
    below_baseline = _candidate(
        structure_score=0.78,
        baseline_min=0.82,
        baseline_within=False,
    )
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [below_baseline]}),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 1
    assert loaded.candidates_accepted == 0
    assert loaded.candidates_rejected == 1
    assert loaded.rejection_reasons == {"below_real_structure_baseline": 1}
    assert loaded.rejected_rows == [
        {
            "candidate_id": "candidate-1",
            "receipt_key": "synthetic-candidate-1#00001",
            "image_id": "synthetic-candidate-1",
            "merchant_name": "Sprouts Farmers Market",
            "operation": "hard_negative",
            "reason": "below_real_structure_baseline",
            "idx": 0,
            "structure_similarity": 0.78,
            "real_baseline_comparison": {
                "candidate_score": 0.78,
                "baseline_min": 0.82,
                "baseline_avg": 0.90,
                "within_real_score_range": False,
                "delta_from_min": -0.04,
                "delta_from_avg": -0.12,
            },
        }
    ]


def test_load_synthetic_training_examples_does_not_hard_gate_sparse_real_baseline(
    tmp_path,
):
    path = tmp_path / "patterns.json"
    sparse_baseline = _candidate(
        structure_score=0.78,
        baseline_min=0.82,
        baseline_within=False,
        baseline_pair_count=1,
    )
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [sparse_baseline]}),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 1
    assert loaded.candidates_accepted == 1
    assert loaded.rejection_reasons == {}
    assert loaded.accepted_real_baseline_comparison == {
        "count": 1,
        "within_real_score_range_count": 0,
        "below_real_score_range_count": 1,
        "within_real_score_range_share": 0.0,
        "candidate_score": {"count": 1, "avg": 0.78, "min": 0.78, "max": 0.78},
        "baseline_avg": {"count": 1, "avg": 0.9, "min": 0.9, "max": 0.9},
        "baseline_min": {"count": 1, "avg": 0.82, "min": 0.82, "max": 0.82},
        "baseline_pair_count": {"count": 1, "avg": 1.0, "min": 1.0, "max": 1.0},
        "delta_from_avg": {"count": 1, "avg": -0.12, "min": -0.12, "max": -0.12},
        "delta_from_min": {"count": 1, "avg": -0.04, "min": -0.04, "max": -0.04},
    }


def test_load_synthetic_training_examples_accepts_safe_replace_field(
    tmp_path,
):
    path = tmp_path / "patterns.json"
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [_replace_field_candidate()]}),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 1
    assert loaded.candidates_accepted == 1
    assert loaded.rejection_reasons == {}
    assert loaded.examples == [
        {
            "tokens": ["MARKET", "05/14/2026", "15:24", "8.00"],
            "bboxes": [
                [410, 950, 500, 975],
                [105, 890, 220, 915],
                [245, 890, 320, 915],
                [830, 525, 885, 550],
            ],
            "ner_tags": [
                "B-MERCHANT_NAME",
                "B-DATE",
                "B-TIME",
                "B-GRAND_TOTAL",
            ],
            "receipt_key": "synthetic-replace-date#00001",
            "image_id": "synthetic-replace-date",
        }
    ]


def test_load_synthetic_training_examples_accepts_matching_merchant_contract(
    tmp_path,
):
    path = tmp_path / "bundle.json"
    path.write_text(
        json.dumps(
            {
                "merchant_synthesis_contracts": [_merchant_contract()],
                "synthetic_training_examples": [_replace_field_candidate()],
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 1
    assert loaded.candidates_accepted == 1
    assert loaded.rejection_reasons == {}


def test_load_synthetic_training_examples_rejects_missing_merchant_contract(
    tmp_path,
):
    path = tmp_path / "bundle.json"
    path.write_text(
        json.dumps(
            {
                "merchant_synthesis_contracts": [
                    _merchant_contract(merchant_name="Market Mart")
                ],
                "synthetic_training_examples": [_candidate()],
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 1
    assert loaded.candidates_accepted == 0
    assert loaded.rejection_reasons == {"missing_merchant_synthesis_contract": 1}


def test_load_synthetic_training_examples_rejects_contract_unsupported_operation(
    tmp_path,
):
    path = tmp_path / "bundle.json"
    add_item = _candidate(candidate_id="add-item")
    add_item["metadata"] = _grounded_add_item_metadata()
    path.write_text(
        json.dumps(
            {
                "merchant_synthesis_contracts": [
                    _merchant_contract(
                        supported_operations=["hard_negative"],
                        operation_contracts={
                            "hard_negative": {"ready": True},
                            "add_line_item": {"ready": False},
                        },
                    )
                ],
                "synthetic_training_examples": [add_item],
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 1
    assert loaded.candidates_accepted == 0
    assert loaded.rejection_reasons == {"operation_not_supported_by_contract": 1}


def test_load_synthetic_training_examples_rejects_contract_unsafe_replace_field(
    tmp_path,
):
    path = tmp_path / "bundle.json"
    contract = _merchant_contract()
    contract["operation_contracts"]["replace_field"]["fields"]["DATE"] = {
        **contract["operation_contracts"]["replace_field"]["fields"]["DATE"],
        "safe_to_mutate": False,
    }
    path.write_text(
        json.dumps(
            {
                "merchant_synthesis_contracts": [contract],
                "synthetic_training_examples": [_replace_field_candidate()],
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 1
    assert loaded.candidates_accepted == 0
    assert loaded.rejection_reasons == {"replace_field_not_contract_safe": 1}


def test_load_synthetic_training_examples_rejects_unsafe_replace_field(
    tmp_path,
):
    path = tmp_path / "patterns.json"
    unsafe = _replace_field_candidate()
    unsafe["metadata"]["mutable_field_evidence"] = {
        **unsafe["metadata"]["mutable_field_evidence"],
        "safe_to_mutate": False,
        "blockers": ["mixed_formats"],
    }
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [unsafe]}),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 1
    assert loaded.candidates_accepted == 0
    assert loaded.rejection_reasons == {"replace_field_not_mutable": 1}
    assert loaded.rejected_rows == [
        {
            "candidate_id": "replace-date",
            "receipt_key": "synthetic-replace-date#00001",
            "image_id": "synthetic-replace-date",
            "merchant_name": "Sprouts Farmers Market",
            "operation": "replace_field",
            "reason": "replace_field_not_mutable",
            "idx": 0,
            "structure_similarity": 0.91,
        }
    ]


def test_load_synthetic_training_examples_rejects_blocked_merchant_artifact(
    tmp_path,
):
    path = tmp_path / "patterns.json"
    path.write_text(
        json.dumps(
            {
                "merchant_name": "Thin Merchant",
                "merchant_receipt_parameterization": {
                    "synthesis_readiness": {
                        "status": "blocked",
                        "score": 0.2,
                        "blockers": [
                            "no_line_items",
                            "no_observed_item_catalog",
                        ],
                    }
                },
                "synthetic_receipt_candidates": [
                    _candidate(merchant_name="Thin Merchant")
                ],
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 1
    assert loaded.candidates_accepted == 0
    assert loaded.candidates_rejected == 1
    assert loaded.rejection_reasons == {"merchant_synthesis_not_ready": 1}
    assert loaded.rejected_rows == [
        {
            "candidate_id": "candidate-1",
            "receipt_key": "synthetic-candidate-1#00001",
            "image_id": "synthetic-candidate-1",
            "merchant_name": "Thin Merchant",
            "operation": "hard_negative",
            "reason": "merchant_synthesis_not_ready",
            "idx": 0,
            "structure_similarity": 0.93,
        }
    ]


def test_load_synthetic_training_examples_rejects_ungrounded_add_item(
    tmp_path,
):
    path = tmp_path / "patterns.json"
    ungrounded = _candidate()
    ungrounded["metadata"] = {
        "source": "sprouts_arithmetic_geometry",
        "operation": "add_line_item",
        "base_receipt_key": "base#00001",
        "added_item": {
            "product_text": "YELLOW BANANAS",
            "category": "PRODUCE",
            "line_total": "1.95",
            "seen_in_other_receipt": False,
        },
        "observed_item_evidence": {
            "product_seen_outside_base": [],
        },
        "arithmetic_reconciliation": {
            "summary_update_policy": "non_taxable_item_delta",
            "tax_delta": "0.00",
        },
        "structure_similarity": {"score": 0.94},
        "layout_integrity": {"score": 1.0},
    }
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [ungrounded]}),
        encoding="utf-8",
    )

    assert _load_synthetic_training_examples(str(path)) == []


def test_load_synthetic_training_examples_rejects_add_item_without_base_category(
    tmp_path,
):
    path = tmp_path / "patterns.json"
    wrong_section = _candidate()
    wrong_section["metadata"] = _grounded_add_item_metadata()
    wrong_section["metadata"]["observed_item_evidence"] = {
        **wrong_section["metadata"]["observed_item_evidence"],
        "base_receipt_has_category": False,
    }
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [wrong_section]}),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 1
    assert loaded.candidates_accepted == 0
    assert loaded.rejection_reasons == {"add_item_base_category_missing": 1}
    assert loaded.rejected_rows == [
        {
            "candidate_id": "candidate-1",
            "receipt_key": "synthetic-candidate-1#00001",
            "image_id": "synthetic-candidate-1",
            "merchant_name": "Sprouts Farmers Market",
            "operation": "add_line_item",
            "reason": "add_item_base_category_missing",
            "idx": 0,
            "category": "PRODUCE",
            "structure_similarity": 0.9,
        }
    ]


def test_load_synthetic_training_examples_rejects_contradictory_optional_evidence(
    tmp_path,
):
    path = tmp_path / "patterns.json"
    wrong_section = _candidate()
    wrong_section["metadata"] = _grounded_add_item_metadata()
    wrong_section["metadata"]["synthesis_accuracy_evidence"]["category_placement"] = {
        **wrong_section["metadata"]["synthesis_accuracy_evidence"][
            "category_placement"
        ],
        "category": "DAIRY",
    }
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [wrong_section]}),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 1
    assert loaded.candidates_accepted == 0
    assert loaded.rejection_reasons == {"add_item_placement_category_mismatch": 1}
    assert loaded.rejected_rows == [
        {
            "candidate_id": "candidate-1",
            "receipt_key": "synthetic-candidate-1#00001",
            "image_id": "synthetic-candidate-1",
            "merchant_name": "Sprouts Farmers Market",
            "operation": "add_line_item",
            "reason": "add_item_placement_category_mismatch",
            "idx": 0,
            "category": "PRODUCE",
            "structure_similarity": 0.9,
        }
    ]


def test_load_synthetic_training_examples_reports_rejection_reasons(tmp_path):
    path = tmp_path / "patterns.json"
    weak = _candidate()
    weak["metadata"]["structure_similarity"]["score"] = 0.31
    ungrounded = _candidate()
    ungrounded["candidate_id"] = "candidate-ungrounded"
    ungrounded["metadata"] = {
        "source": "sprouts_arithmetic_geometry",
        "operation": "add_line_item",
        "base_receipt_key": "base#00001",
        "added_item": {
            "product_text": "YELLOW BANANAS",
            "category": "PRODUCE",
            "line_total": "1.95",
            "seen_in_other_receipt": False,
        },
        "observed_item_evidence": {
            "product_seen_outside_base": [],
        },
        "arithmetic_reconciliation": {
            "summary_update_policy": "non_taxable_item_delta",
            "tax_delta": "0.00",
        },
        "structure_similarity": {"score": 0.94},
        "layout_integrity": {"score": 1.0},
    }
    path.write_text(
        json.dumps(
            {
                "synthetic_receipt_candidates": [
                    _candidate(),
                    weak,
                    ungrounded,
                    _candidate(train_only=False),
                ]
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 4
    assert loaded.candidates_accepted == 1
    assert loaded.candidates_rejected == 3
    assert loaded.rejection_reasons == {
        "low_structure_similarity": 1,
        "add_item_not_cross_receipt_grounded": 1,
        "not_train_only": 1,
    }
    assert [row["reason"] for row in loaded.rejected_rows] == [
        "low_structure_similarity",
        "add_item_not_cross_receipt_grounded",
        "not_train_only",
    ]
    assert [row["merchant_name"] for row in loaded.rejected_rows] == [
        "Sprouts Farmers Market",
        "Sprouts Farmers Market",
        "Sprouts Farmers Market",
    ]


def test_load_synthetic_training_examples_caps_same_merchant_operation(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("LAYOUTLM_SYNTHETIC_MAX_PER_MERCHANT", "10")
    monkeypatch.setenv("LAYOUTLM_SYNTHETIC_MAX_PER_MERCHANT_OPERATION", "2")
    path = tmp_path / "patterns.json"
    path.write_text(
        json.dumps(
            {
                "synthetic_receipt_candidates": [
                    _candidate(
                        candidate_id="weak-hard-negative",
                        structure_score=0.70,
                    ),
                    _candidate(
                        candidate_id="best-hard-negative",
                        structure_score=0.97,
                    ),
                    _candidate(
                        candidate_id="second-hard-negative",
                        structure_score=0.94,
                    ),
                ]
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 3
    assert loaded.candidates_accepted == 2
    assert loaded.candidates_rejected == 1
    assert loaded.rejection_reasons == {
        "merchant_operation_synthetic_cap": 1,
    }
    assert [example["receipt_key"] for example in loaded.examples] == [
        "synthetic-best-hard-negative#00001",
        "synthetic-second-hard-negative#00001",
    ]


def test_load_synthetic_training_examples_caps_same_merchant(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("LAYOUTLM_SYNTHETIC_MAX_PER_MERCHANT", "2")
    monkeypatch.setenv("LAYOUTLM_SYNTHETIC_MAX_PER_MERCHANT_OPERATION", "5")
    path = tmp_path / "patterns.json"
    add_item = _candidate(
        candidate_id="add-item",
        structure_score=0.90,
    )
    add_item["metadata"] = _grounded_add_item_metadata()
    path.write_text(
        json.dumps(
            {
                "synthetic_receipt_candidates": [
                    _candidate(
                        candidate_id="hard-negative",
                        structure_score=0.93,
                    ),
                    _candidate(
                        candidate_id="lower-hard-negative",
                        structure_score=0.86,
                    ),
                    add_item,
                ]
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 3
    assert loaded.candidates_accepted == 2
    assert loaded.candidates_rejected == 1
    assert loaded.rejection_reasons == {"merchant_synthetic_cap": 1}
    assert [example["receipt_key"] for example in loaded.examples] == [
        "synthetic-add-item#00001",
        "synthetic-hard-negative#00001",
    ]
    assert loaded.accepted_operation_counts == {
        "add_line_item": 1,
        "hard_negative": 1,
    }
    assert loaded.accepted_mix_balance == {
        "accepted_count": 2,
        "merchant_count": 1,
        "operation_count": 2,
        "top_merchant": "Sprouts Farmers Market",
        "top_merchant_count": 2,
        "top_merchant_share": 1.0,
        "top_operation": "hard_negative",
        "top_operation_count": 1,
        "top_operation_share": 0.5,
        "merchant_entropy": 0.0,
        "operation_entropy": 1.0,
        "risk_level": "low",
        "risk_reasons": ["too_few_examples_for_balance_assessment"],
    }
    assert loaded.accepted_category_counts == {"PRODUCE": 1}
    assert loaded.accepted_field_replacement_counts == {}
    assert loaded.accepted_structure_similarity == {
        "count": 2,
        "avg": 0.915,
        "min": 0.9,
        "max": 0.93,
    }
    assert loaded.accepted_real_baseline_comparison == {
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
    }
    assert loaded.accepted_structure_components == {
        "category_sequence": {
            "count": 2,
            "avg": 0.67,
            "min": 0.67,
            "max": 0.67,
        },
        "category_set": {
            "count": 2,
            "avg": 0.5,
            "min": 0.5,
            "max": 0.5,
        },
        "item_count": {
            "count": 2,
            "avg": 0.415,
            "min": 0.33,
            "max": 0.5,
        },
        "line_step": {
            "count": 2,
            "avg": 0.6,
            "min": 0.55,
            "max": 0.65,
        },
        "price_column": {
            "count": 2,
            "avg": 1.0,
            "min": 1.0,
            "max": 1.0,
        },
        "token_count": {
            "count": 2,
            "avg": 0.47,
            "min": 0.47,
            "max": 0.47,
        },
    }
    assert loaded.accepted_grounded_count == 1
    assert loaded.accepted_arithmetic_count == 1


def test_load_synthetic_training_examples_covers_ready_operations_before_duplicate_operation(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("LAYOUTLM_SYNTHETIC_MAX_PER_MERCHANT", "2")
    monkeypatch.setenv("LAYOUTLM_SYNTHETIC_MAX_PER_MERCHANT_OPERATION", "5")
    path = tmp_path / "bundle.json"
    best_hard_negative = _candidate(
        candidate_id="best-hard-negative",
        structure_score=0.97,
    )
    duplicate_hard_negative = _candidate(
        candidate_id="duplicate-hard-negative",
        structure_score=0.96,
    )
    replace_field = _replace_field_candidate()
    replace_field["metadata"]["candidate_quality"] = {
        "schema_version": "synthetic-candidate-quality-v1",
        "score": 0.80,
        "high_fidelity": True,
        "components": {"structure_similarity": 0.91},
    }
    path.write_text(
        json.dumps(
            {
                "merchant_synthesis_contracts": [_merchant_contract()],
                "synthetic_training_examples": [
                    best_hard_negative,
                    duplicate_hard_negative,
                    replace_field,
                ],
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 3
    assert loaded.candidates_accepted == 2
    assert loaded.candidates_rejected == 1
    assert loaded.rejection_reasons == {"merchant_synthetic_cap": 1}
    assert [row["candidate_id"] for row in loaded.accepted_rows] == [
        "best-hard-negative",
        "replace-date",
    ]
    assert loaded.accepted_operation_counts == {
        "hard_negative": 1,
        "replace_field": 1,
    }
    assert loaded.accepted_operation_coverage["ready_operation_count"] == 4
    assert loaded.accepted_operation_coverage["accepted_ready_operation_count"] == 2
    assert loaded.accepted_operation_coverage["accepted_ready_operation_share"] == 0.5
    assert loaded.accepted_operation_coverage["uncovered_ready_operations"] == [
        "add_line_item",
        "remove_line_item",
    ]
    assert loaded.accepted_operation_coverage["operations"]["replace_field"] == {
        "ready_merchant_count": 1,
        "accepted_merchant_count": 1,
        "accepted_ready_merchant_count": 1,
        "accepted_count": 1,
        "ready_acceptance_share": 1.0,
        "ready_merchants": ["Sprouts Farmers Market"],
        "accepted_merchants": ["Sprouts Farmers Market"],
        "uncovered_ready_merchants": [],
    }
    assert loaded.accepted_field_replacement_counts == {"DATE": 1}
    assert loaded.rejected_rows[0]["candidate_id"] == "duplicate-hard-negative"


def test_load_synthetic_training_examples_prefers_richer_add_item_evidence(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("LAYOUTLM_SYNTHETIC_MAX_PER_MERCHANT", "10")
    monkeypatch.setenv("LAYOUTLM_SYNTHETIC_MAX_PER_MERCHANT_OPERATION", "1")
    path = tmp_path / "patterns.json"
    plain = _candidate(candidate_id="plain-add")
    plain_metadata = _grounded_add_item_metadata()
    del plain_metadata["synthesis_accuracy_evidence"]
    plain["metadata"] = plain_metadata
    rich = _candidate(candidate_id="rich-add")
    rich["metadata"] = _grounded_add_item_metadata()
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [plain, rich]}),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 2
    assert loaded.candidates_accepted == 1
    assert loaded.candidates_rejected == 1
    assert loaded.rejection_reasons == {"merchant_operation_synthetic_cap": 1}
    assert [row["candidate_id"] for row in loaded.accepted_rows] == ["rich-add"]
    assert loaded.rejected_rows == [
        {
            "candidate_id": "plain-add",
            "receipt_key": "synthetic-plain-add#00001",
            "image_id": "synthetic-plain-add",
            "merchant_name": "Sprouts Farmers Market",
            "operation": "add_line_item",
            "reason": "merchant_operation_synthetic_cap",
            "idx": 0,
            "category": "PRODUCE",
            "structure_similarity": 0.9,
        }
    ]


def test_load_synthetic_training_examples_prefers_declared_candidate_quality(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("LAYOUTLM_SYNTHETIC_MAX_PER_MERCHANT", "10")
    monkeypatch.setenv("LAYOUTLM_SYNTHETIC_MAX_PER_MERCHANT_OPERATION", "1")
    path = tmp_path / "patterns.json"
    lower = _candidate(candidate_id="lower-quality-add")
    lower["metadata"] = {
        **_grounded_add_item_metadata(),
        "candidate_quality": {
            "schema_version": "synthetic-candidate-quality-v1",
            "score": 0.72,
            "high_fidelity": False,
            "components": {"structure_similarity": 0.9},
        },
    }
    higher = _candidate(candidate_id="higher-quality-add")
    higher["metadata"] = {
        **_grounded_add_item_metadata(),
        "candidate_quality": {
            "schema_version": "synthetic-candidate-quality-v1",
            "score": 0.96,
            "high_fidelity": True,
            "components": {"structure_similarity": 0.9},
        },
    }
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [lower, higher]}),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 2
    assert loaded.candidates_accepted == 1
    assert loaded.rejection_reasons == {"merchant_operation_synthetic_cap": 1}
    assert [row["candidate_id"] for row in loaded.accepted_rows] == [
        "higher-quality-add"
    ]
    assert loaded.accepted_candidate_quality == {
        "count": 1,
        "avg": 0.96,
        "min": 0.96,
        "max": 0.96,
    }
    assert loaded.accepted_candidate_quality_components == {
        "structure_similarity": {
            "count": 1,
            "avg": 0.9,
            "min": 0.9,
            "max": 0.9,
        }
    }
    assert loaded.rejected_rows == [
        {
            "candidate_id": "lower-quality-add",
            "receipt_key": "synthetic-lower-quality-add#00001",
            "image_id": "synthetic-lower-quality-add",
            "merchant_name": "Sprouts Farmers Market",
            "operation": "add_line_item",
            "reason": "merchant_operation_synthetic_cap",
            "idx": 0,
            "category": "PRODUCE",
            "structure_similarity": 0.9,
            "candidate_quality": 0.72,
        }
    ]


def test_load_synthetic_training_examples_accepts_grounded_remove_item(
    tmp_path,
):
    path = tmp_path / "patterns.json"
    grounded = _candidate()
    grounded["metadata"] = {
        "source": "merchant_arithmetic_geometry",
        "operation": "remove_line_item",
        "base_receipt_key": "base#00001",
        "removed_item": {
            "product_text": "PEARS",
            "category": "PRODUCE",
            "line_total": "2.00",
            "taxable": False,
        },
        "arithmetic_reconciliation": {
            "summary_update_policy": "non_taxable_item_delta",
            "tax_delta": "0.00",
        },
        "structure_similarity": {"score": 0.88},
        "layout_integrity": {"score": 1.0},
    }
    path.write_text(
        json.dumps({"synthetic_receipt_candidates": [grounded]}),
        encoding="utf-8",
    )

    examples = _load_synthetic_training_examples(str(path))

    assert [example["receipt_key"] for example in examples] == [
        "synthetic-candidate-1#00001"
    ]


def test_load_synthetic_training_examples_accepts_local_artifact_directory(
    tmp_path,
):
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    first.write_text(
        json.dumps({"synthetic_receipt_candidates": [_candidate()]}),
        encoding="utf-8",
    )
    other = _candidate()
    other["candidate_id"] = "candidate-2"
    other["image_id"] = "synthetic-candidate-2"
    other["receipt_key"] = "synthetic-candidate-2#00001"
    second.write_text(
        json.dumps({"line_item_patterns": {"synthetic_receipt_candidates": [other]}}),
        encoding="utf-8",
    )

    examples = _load_synthetic_training_examples(str(tmp_path))

    assert [example["receipt_key"] for example in examples] == [
        "synthetic-candidate-1#00001",
        "synthetic-candidate-2#00001",
    ]


def test_load_synthetic_training_examples_accepts_s3_prefix(monkeypatch):
    objects = {
        "line_item_patterns/run/a.json": {
            "synthetic_receipt_candidates": [_candidate()]
        },
        "line_item_patterns/run/b.json": {
            "synthetic_receipt_candidates": [
                {
                    **_candidate(),
                    "candidate_id": "candidate-2",
                    "image_id": "synthetic-candidate-2",
                    "receipt_key": "synthetic-candidate-2#00001",
                }
            ]
        },
        "line_item_patterns/run/readme.txt": {"ignored": True},
    }

    class Body:
        def __init__(self, data):
            self.data = data

        def read(self):
            return json.dumps(self.data).encode("utf-8")

    class Paginator:
        def paginate(self, Bucket, Prefix):
            assert Bucket == "bucket"
            assert Prefix == "line_item_patterns/run/"
            yield {
                "Contents": [
                    {"Key": key}
                    for key in reversed(sorted(objects))
                    if key.startswith(Prefix)
                ]
            }

    class S3Client:
        def get_paginator(self, name):
            assert name == "list_objects_v2"
            return Paginator()

        def get_object(self, Bucket, Key):
            assert Bucket == "bucket"
            return {"Body": Body(objects[Key])}

    monkeypatch.setitem(
        sys.modules,
        "boto3",
        types.SimpleNamespace(client=lambda service: S3Client()),
    )

    examples = _load_synthetic_training_examples("s3://bucket/line_item_patterns/run/")

    assert [example["receipt_key"] for example in examples] == [
        "synthetic-candidate-1#00001",
        "synthetic-candidate-2#00001",
    ]


def test_require_high_fidelity_rejects_non_high_fidelity_rows(monkeypatch):
    """The opt-in require_high_fidelity gate rejects candidates that clear the
    other gates but are not flagged high-fidelity."""
    from receipt_layoutlm.data_loader import _select_synthetic_training_examples

    # Isolate the high-fidelity gate from the structure/contract quality gate.
    monkeypatch.setenv("LAYOUTLM_SYNTHETIC_QUALITY_GATE", "0")

    def row(cid, high_fidelity):
        return {
            "train_only": True,
            "tokens": ["MILK", "5.00"],
            "bboxes": [[10, 900, 90, 924], [800, 900, 880, 924]],
            "ner_tags": ["B-PRODUCT_NAME", "B-LINE_TOTAL"],
            "candidate_id": cid,
            "image_id": f"synthetic-{cid}",
            "receipt_key": f"synthetic-{cid}#00001",
            "merchant_name": "Test Mart",
            "metadata": {
                "merchant_name": "Test Mart",
                "candidate_quality": {"high_fidelity": high_fidelity},
            },
        }

    rows = [row("hi", True), row("lo", False)]
    # Default: high-fidelity not required -> both pass the remaining gates.
    default = _select_synthetic_training_examples(rows)
    assert default.candidates_accepted == 2
    # Opt-in: the non-high-fidelity row is rejected with the explicit reason.
    strict = _select_synthetic_training_examples(rows, require_high_fidelity=True)
    assert strict.rejection_reasons.get("not_high_fidelity") == 1
    assert strict.candidates_accepted == 1


def _compose_online_catalog_candidate(
    *, label_control_ok=True, rate_stable=True
):
    """A template-filled compose_online_catalog candidate with clean,
    self-assigned item-region labels."""
    candidate = _candidate(candidate_id="compose-1")
    candidate["tokens"] = [
        "012345678905",
        "WIDGET",
        "PRO",
        "<A>",
        "$12.50",
        "SUBTOTAL",
        "12.50",
        "TAX",
        "0.91",
        "TOTAL",
        "13.41",
    ]
    candidate["bboxes"] = [
        [8, 700, 220, 722],
        [230, 700, 320, 722],
        [330, 700, 400, 722],
        [705, 700, 760, 722],
        [820, 700, 900, 722],
        [500, 600, 600, 622],
        [820, 600, 900, 622],
        [500, 575, 545, 597],
        [830, 575, 900, 597],
        [500, 545, 560, 567],
        [820, 545, 900, 567],
    ]
    candidate["ner_tags"] = [
        "O",
        "B-PRODUCT_NAME",
        "I-PRODUCT_NAME",
        "O",
        "B-LINE_TOTAL",
        "O",
        "B-SUBTOTAL",
        "O",
        "B-TAX",
        "O",
        "B-GRAND_TOTAL",
    ]
    metadata = candidate["metadata"]
    metadata["source"] = "merchant_online_catalog"
    metadata["operation"] = "compose_online_catalog"
    metadata["online_catalog_grounding"] = {
        "all_priced": True,
        "all_named": True,
        "source": "merchant_online_catalog",
    }
    metadata["label_control"] = {
        "item_token_count": 5,
        "correctly_labeled": 5 if label_control_ok else 4,
        "all_correct": label_control_ok,
    }
    metadata["arithmetic_reconciliation"] = {
        "summary_update_policy": "composed_catalog_totals",
        "new_subtotal": "12.50",
        "new_tax": "0.91",
        "new_grand_total": "13.41",
        "tax_rate": "0.0726",
        "tax_basis": "effective_rate_on_subtotal",
        "tax_rate_stable": rate_stable,
        "subtotal_consistent": True,
    }
    metadata["candidate_quality"] = {"high_fidelity": True, "score": 0.95}
    metadata["layout_integrity"] = {
        "score": 1.0,
        "edit_introduced_overlap_pair_count": 0,
        "invalid_word_box_count": 0,
    }
    return candidate


def test_load_synthetic_training_examples_accepts_compose_online_catalog(
    tmp_path,
):
    path = tmp_path / "patterns.json"
    path.write_text(
        json.dumps(
            {
                "synthetic_receipt_candidates": [
                    _compose_online_catalog_candidate()
                ]
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_seen == 1
    assert loaded.candidates_accepted == 1
    assert loaded.rejection_reasons == {}


def test_load_synthetic_training_examples_rejects_compose_with_uncontrolled_labels(
    tmp_path,
):
    """The clean-supervision guarantee is enforced: a composed receipt whose
    item tokens are not all correctly labeled is rejected."""
    path = tmp_path / "patterns.json"
    path.write_text(
        json.dumps(
            {
                "synthetic_receipt_candidates": [
                    _compose_online_catalog_candidate(label_control_ok=False)
                ]
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_accepted == 0
    assert loaded.rejection_reasons == {"compose_item_labels_uncontrolled": 1}


def test_load_synthetic_training_examples_rejects_compose_with_unstable_tax(
    tmp_path,
):
    """Composing taxable receipts requires a stable observed rate."""
    path = tmp_path / "patterns.json"
    path.write_text(
        json.dumps(
            {
                "synthetic_receipt_candidates": [
                    _compose_online_catalog_candidate(rate_stable=False)
                ]
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_synthetic_training_examples_with_summary(str(path))

    assert loaded.candidates_accepted == 0
    assert loaded.rejection_reasons == {"invalid_arithmetic_reconciliation": 1}


def _add_item_row(*, layout_score=1.0, insertion_valid=True):
    """A grounded add_line_item row carrying explicit geometry flags."""
    metadata = _grounded_add_item_metadata()
    metadata["added_item"]["insertion_position_valid"] = insertion_valid
    # Declared quality clears the 0.70 threshold and is NOT high-fidelity, so
    # only the new hard-geometry checks can reject it.
    metadata["candidate_quality"] = {"high_fidelity": False, "score": 0.95}
    metadata["layout_integrity"] = {"score": layout_score}
    return {
        "train_only": True,
        "merchant_name": "Sprouts Farmers Market",
        "image_id": "synthetic-addgeom",
        "receipt_key": "synthetic-addgeom#00001",
        "tokens": ["YELLOW", "BANANAS", "1.95"],
        "bboxes": [[80, 600, 180, 625], [185, 600, 320, 625], [820, 600, 890, 625]],
        "ner_tags": ["B-PRODUCT_NAME", "I-PRODUCT_NAME", "B-LINE_TOTAL"],
        "metadata": metadata,
    }


def test_load_synthetic_training_examples_rejects_failed_layout_on_default_path():
    """A failed layout_integrity must reject on the ORDINARY path, not only when
    require_high_fidelity is set — training on overlapping geometry is invalid
    regardless of the declared candidate-quality score."""
    from receipt_layoutlm.data_loader import _select_synthetic_training_examples

    loaded = _select_synthetic_training_examples([_add_item_row(layout_score=0.0)])

    assert loaded.candidates_accepted == 0
    assert loaded.rejection_reasons == {"layout_integrity_failed": 1}


def test_load_synthetic_training_examples_rejects_invalid_insertion_on_default_path():
    """An item the generator flagged as inserted below the summary block is
    rejected on the ordinary path too."""
    from receipt_layoutlm.data_loader import _select_synthetic_training_examples

    loaded = _select_synthetic_training_examples(
        [_add_item_row(insertion_valid=False)]
    )

    assert loaded.candidates_accepted == 0
    assert loaded.rejection_reasons == {"insertion_position_invalid": 1}


def test_load_synthetic_training_examples_accepts_clean_add_item_geometry():
    """The same row with clean geometry flags is accepted (the new checks do not
    over-reject)."""
    from receipt_layoutlm.data_loader import _select_synthetic_training_examples

    loaded = _select_synthetic_training_examples([_add_item_row()])

    assert loaded.candidates_accepted == 1
    assert loaded.rejection_reasons == {}
