"""Tests for synthetic augmentation audit helpers."""

from dataclasses import dataclass

import pytest

from receipt_agent.agents.label_evaluator.augmentation_audit import (
    build_synthetic_augmentation_audit_from_dynamo,
    build_synthetic_augmentation_audit,
    confusion_pair_count,
    metrics_summary_from_job_metrics,
    resolve_job_id_from_ref,
    target_pairs_from_pattern_artifact,
    target_pairs_from_plan,
)


@dataclass
class FakeJob:
    job_id: str
    name: str
    created_at: str


@dataclass
class FakeMetric:
    metric_name: str
    value: object
    epoch: int | None


class FakeDynamoClient:
    def __init__(self):
        self.jobs = [
            FakeJob(
                "11111111-1111-4111-8111-111111111111",
                "layoutlm-baseline",
                "2026-05-01T00:00:00Z",
            ),
            FakeJob(
                "22222222-2222-4222-8222-222222222222",
                "layoutlm-augmented",
                "2026-05-02T00:00:00Z",
            ),
        ]
        self.metrics_by_job_id = {
            "11111111-1111-4111-8111-111111111111": [
                FakeMetric("val_f1", 0.81, 2),
                FakeMetric("train_loss", 0.35, 2),
                FakeMetric("eval_loss", 0.32, 2),
                FakeMetric("confusion_matrix", _cm(7), 2),
            ],
            "22222222-2222-4222-8222-222222222222": [
                FakeMetric("val_f1", 0.824, 2),
                FakeMetric("train_loss", 0.29, 2),
                FakeMetric("eval_loss", 0.30, 2),
                FakeMetric("confusion_matrix", _cm(3), 2),
                FakeMetric("synthetic_train_examples", 48, None),
                FakeMetric("synthetic_candidates_seen", 54, None),
                FakeMetric("synthetic_candidates_accepted", 48, None),
                FakeMetric("synthetic_candidates_rejected", 6, None),
                FakeMetric(
                    "synthetic_rejection_reasons",
                    {
                        "low_structure_similarity": 4,
                        "add_item_not_cross_receipt_grounded": 2,
                        "unused_zero_reason": 0,
                    },
                    None,
                ),
                FakeMetric(
                    "synthetic_accepted_candidate_quality",
                    {"count": 48, "avg": 0.88, "min": 0.79, "max": 0.96},
                    None,
                ),
                FakeMetric(
                    "synthetic_accepted_candidate_quality_components",
                    {
                        "structure_similarity": {
                            "count": 48,
                            "avg": 0.91,
                            "min": 0.84,
                            "max": 0.97,
                        },
                        "cross_receipt_grounding": {
                            "count": 12,
                            "avg": 1.0,
                            "min": 1.0,
                            "max": 1.0,
                        },
                    },
                    None,
                ),
                FakeMetric(
                    "synthetic_accepted_mix_balance",
                    {
                        "accepted_count": 48,
                        "merchant_count": 4,
                        "operation_count": 3,
                        "top_merchant": "Sprouts Farmers Market",
                        "top_merchant_count": 18,
                        "top_merchant_share": 0.375,
                        "top_operation": "replace_field",
                        "top_operation_count": 20,
                        "top_operation_share": 0.417,
                        "merchant_entropy": 0.93,
                        "operation_entropy": 0.91,
                        "risk_level": "low",
                        "risk_reasons": [],
                    },
                    None,
                ),
                FakeMetric(
                    "synthetic_accepted_real_baseline_comparison",
                    {
                        "count": 48,
                        "within_real_score_range_count": 48,
                        "below_real_score_range_count": 0,
                        "within_real_score_range_share": 1.0,
                        "candidate_score": {
                            "count": 48,
                            "avg": 0.91,
                            "min": 0.84,
                            "max": 0.97,
                        },
                        "baseline_min": {
                            "count": 48,
                            "avg": 0.79,
                            "min": 0.74,
                            "max": 0.83,
                        },
                        "baseline_pair_count": {
                            "count": 48,
                            "avg": 7.0,
                            "min": 4.0,
                            "max": 12.0,
                        },
                    },
                    None,
                ),
            ],
        }

    def get_job_by_name(self, name, limit=None):
        matches = [job for job in self.jobs if job.name == name]
        return matches[:limit], None

    def list_job_metrics(self, job_id, last_evaluated_key=None):
        assert last_evaluated_key is None
        return self.metrics_by_job_id[job_id], None


def _cm(merchant_to_o: int, o_to_merchant: int = 0):
    return {
        "labels": ["B-MERCHANT_NAME", "I-MERCHANT_NAME", "O"],
        "matrix": [
            [20, 0, merchant_to_o],
            [0, 18, merchant_to_o],
            [o_to_merchant, 0, 100],
        ],
    }


def test_confusion_pair_count_collapses_bio_labels():
    assert confusion_pair_count(_cm(7), "MERCHANT_NAME", "O") == 14


def test_target_pairs_from_plan_extracts_unique_recipe_pairs():
    plan = {
        "recipes": [
            {"actual_label": "B-MERCHANT_NAME", "predicted_label": "O"},
            {"actual_label": "MERCHANT_NAME", "predicted_label": "O"},
            {"actual_label": "O", "predicted_label": "ADDRESS_LINE"},
        ]
    }

    assert target_pairs_from_plan(plan) == [
        ("MERCHANT_NAME", "O"),
        ("O", "ADDRESS_LINE"),
    ]


def test_target_pairs_from_pattern_artifact_accepts_saved_builder_output():
    artifact = {
        "synthetic_receipt_plan": {
            "recipes": [
                {
                    "actual_label": "B-MERCHANT_NAME",
                    "predicted_label": "O",
                }
            ]
        }
    }

    assert target_pairs_from_pattern_artifact(artifact) == [
        ("MERCHANT_NAME", "O")
    ]


def test_metrics_summary_selects_best_f1_confusion_and_latest_losses():
    metrics = [
        FakeMetric("val_f1", 0.72, 1),
        FakeMetric("val_f1", 0.81, 2),
        FakeMetric("confusion_matrix", _cm(9), 1),
        FakeMetric("confusion_matrix", _cm(4), 2),
        FakeMetric("train_loss", 0.42, 1),
        FakeMetric("train_loss", 0.21, 3),
        FakeMetric("eval_loss", 0.31, 1),
        FakeMetric("eval_loss", 0.29, 3),
        FakeMetric("synthetic_train_examples", 48, None),
        FakeMetric("synthetic_candidates_seen", 54, None),
        FakeMetric("synthetic_candidates_accepted", 48, None),
        FakeMetric("synthetic_candidates_rejected", 6, None),
        FakeMetric(
            "synthetic_rejection_reasons",
            {
                "low_structure_similarity": 4,
                "add_item_not_cross_receipt_grounded": 2,
                "missing_metadata": 0,
            },
            None,
        ),
        FakeMetric(
            "synthetic_accepted_operation_counts",
            {"add_line_item": 12, "replace_field": 36},
            None,
        ),
        FakeMetric(
            "synthetic_accepted_category_counts",
            {"PRODUCE": 8, "DAIRY": 0},
            None,
        ),
        FakeMetric(
            "synthetic_accepted_field_replacement_counts",
            {"DATE": 20, "TIME": 16},
            None,
        ),
        FakeMetric(
            "synthetic_accepted_structure_similarity",
            {"count": 48, "avg": 0.91, "min": 0.84, "max": 0.97},
            None,
        ),
        FakeMetric(
            "synthetic_accepted_structure_components",
            {
                "price_column": {
                    "count": 48,
                    "avg": 0.98,
                    "min": 0.91,
                    "max": 1.0,
                },
                "line_step": {
                    "count": 48,
                    "avg": 0.72,
                    "min": 0.52,
                    "max": 0.88,
                },
            },
            None,
        ),
        FakeMetric(
            "synthetic_accepted_candidate_quality",
            {"count": 48, "avg": 0.88, "min": 0.79, "max": 0.96},
            None,
        ),
        FakeMetric(
            "synthetic_accepted_candidate_quality_components",
            {
                "structure_similarity": {
                    "count": 48,
                    "avg": 0.91,
                    "min": 0.84,
                    "max": 0.97,
                },
                "cross_receipt_grounding": {
                    "count": 12,
                    "avg": 1.0,
                    "min": 1.0,
                    "max": 1.0,
                },
            },
            None,
        ),
        FakeMetric(
            "synthetic_accepted_mix_balance",
            {
                "accepted_count": 48,
                "merchant_count": 4,
                "operation_count": 3,
                "top_merchant": "Sprouts Farmers Market",
                "top_merchant_count": 18,
                "top_merchant_share": 0.375,
                "top_operation": "replace_field",
                "top_operation_count": 20,
                "top_operation_share": 0.417,
                "merchant_entropy": 0.93,
                "operation_entropy": 0.91,
                "risk_level": "low",
                "risk_reasons": [],
            },
            None,
        ),
        FakeMetric(
            "synthetic_accepted_real_baseline_comparison",
            {
                "count": 48,
                "within_real_score_range_count": 48,
                "below_real_score_range_count": 0,
                "within_real_score_range_share": 1.0,
                "candidate_score": {
                    "count": 48,
                    "avg": 0.91,
                    "min": 0.84,
                    "max": 0.97,
                },
                "baseline_min": {
                    "count": 48,
                    "avg": 0.79,
                    "min": 0.74,
                    "max": 0.83,
                },
                "baseline_pair_count": {
                    "count": 48,
                    "avg": 7,
                    "min": 4,
                    "max": 12,
                },
            },
            None,
        ),
        FakeMetric("synthetic_accepted_grounded_count", 12, None),
        FakeMetric("synthetic_accepted_arithmetic_count", 12, None),
    ]

    summary = metrics_summary_from_job_metrics(metrics)

    assert summary["best_epoch"] == 2
    assert summary["val_f1"] == 0.81
    assert summary["confusion_matrix"] == _cm(4)
    assert summary["train_loss"] == 0.21
    assert summary["eval_loss"] == 0.29
    assert summary["synthetic_train_examples"] == 48
    assert summary["synthetic_candidates_seen"] == 54
    assert summary["synthetic_candidates_accepted"] == 48
    assert summary["synthetic_candidates_rejected"] == 6
    assert summary["synthetic_rejection_reasons"] == {
        "add_item_not_cross_receipt_grounded": 2,
        "low_structure_similarity": 4,
    }
    assert summary["synthetic_accepted_operation_counts"] == {
        "add_line_item": 12,
        "replace_field": 36,
    }
    assert summary["synthetic_accepted_category_counts"] == {"PRODUCE": 8}
    assert summary["synthetic_accepted_field_replacement_counts"] == {
        "DATE": 20,
        "TIME": 16,
    }
    assert summary["synthetic_accepted_structure_similarity"] == {
        "count": 48,
        "avg": 0.91,
        "min": 0.84,
        "max": 0.97,
    }
    assert summary["synthetic_accepted_structure_components"] == {
        "price_column": {"count": 48, "avg": 0.98, "min": 0.91, "max": 1.0},
        "line_step": {"count": 48, "avg": 0.72, "min": 0.52, "max": 0.88},
    }
    assert summary["synthetic_accepted_candidate_quality"] == {
        "count": 48,
        "avg": 0.88,
        "min": 0.79,
        "max": 0.96,
    }
    assert summary["synthetic_accepted_candidate_quality_components"] == {
        "structure_similarity": {
            "count": 48,
            "avg": 0.91,
            "min": 0.84,
            "max": 0.97,
        },
        "cross_receipt_grounding": {
            "count": 12,
            "avg": 1.0,
            "min": 1.0,
            "max": 1.0,
        },
    }
    assert summary["synthetic_accepted_mix_balance"] == {
        "accepted_count": 48,
        "merchant_count": 4,
        "operation_count": 3,
        "top_merchant": "Sprouts Farmers Market",
        "top_merchant_count": 18,
        "top_merchant_share": 0.375,
        "top_operation": "replace_field",
        "top_operation_count": 20,
        "top_operation_share": 0.417,
        "merchant_entropy": 0.93,
        "operation_entropy": 0.91,
        "risk_level": "low",
    }
    assert summary["synthetic_accepted_real_baseline_comparison"] == {
        "count": 48,
        "within_real_score_range_count": 48,
        "below_real_score_range_count": 0,
        "within_real_score_range_share": 1.0,
        "candidate_score": {
            "count": 48,
            "avg": 0.91,
            "min": 0.84,
            "max": 0.97,
        },
        "baseline_min": {
            "count": 48,
            "avg": 0.79,
            "min": 0.74,
            "max": 0.83,
        },
        "baseline_pair_count": {
            "count": 48,
            "avg": 7.0,
            "min": 4.0,
            "max": 12.0,
        },
    }
    assert summary["synthetic_accepted_grounded_count"] == 12
    assert summary["synthetic_accepted_arithmetic_count"] == 12


def test_audit_promotes_when_target_confusions_improve_without_overtraining():
    audit = build_synthetic_augmentation_audit(
        baseline_job_id="baseline",
        augmented_job_id="augmented",
        baseline_metrics={
            "val_f1": 0.81,
            "train_loss": 0.35,
            "eval_loss": 0.32,
            "confusion_matrix": _cm(7),
        },
        augmented_metrics={
            "val_f1": 0.824,
            "train_loss": 0.29,
            "eval_loss": 0.30,
            "confusion_matrix": _cm(3),
            "synthetic_train_examples": 48,
            "synthetic_candidates_seen": 54,
            "synthetic_candidates_accepted": 48,
            "synthetic_candidates_rejected": 6,
        },
        target_pairs=[("MERCHANT_NAME", "O")],
    )

    assert audit.recommendation == "promote"
    assert audit.synthetic_train_examples == 48
    assert audit.total_before_count == 14
    assert audit.total_after_count == 6
    assert audit.improved_pair_count == 1
    assert audit.overtraining_signal is False
    assert audit.synthetic_quality_signal is False
    assert audit.synthetic_acceptance_rate == pytest.approx(0.8889)
    assert audit.val_f1_delta == pytest.approx(0.014)
    assert audit.synthetic_mix_balance_signal is False


def test_audit_holds_when_accepted_mix_is_concentrated():
    audit = build_synthetic_augmentation_audit(
        baseline_job_id="baseline",
        augmented_job_id="augmented",
        baseline_metrics={
            "val_f1": 0.81,
            "train_loss": 0.35,
            "eval_loss": 0.32,
            "confusion_matrix": _cm(7),
        },
        augmented_metrics={
            "val_f1": 0.824,
            "train_loss": 0.29,
            "eval_loss": 0.30,
            "confusion_matrix": _cm(3),
            "synthetic_train_examples": 48,
            "synthetic_candidates_seen": 54,
            "synthetic_candidates_accepted": 48,
            "synthetic_candidates_rejected": 6,
            "synthetic_accepted_mix_balance": {
                "accepted_count": 48,
                "merchant_count": 1,
                "operation_count": 2,
                "top_merchant": "Sprouts Farmers Market",
                "top_merchant_count": 48,
                "top_merchant_share": 1.0,
                "top_operation": "add_line_item",
                "top_operation_count": 34,
                "top_operation_share": 0.708,
                "merchant_entropy": 0.0,
                "operation_entropy": 0.78,
                "risk_level": "high",
                "risk_reasons": ["single_merchant_accepted"],
            },
        },
        target_pairs=[("MERCHANT_NAME", "O")],
    )

    assert audit.recommendation == "hold"
    assert audit.overtraining_signal is False
    assert audit.synthetic_quality_signal is True
    assert audit.synthetic_mix_balance_signal is True
    assert audit.synthetic_accepted_mix_balance["risk_level"] == "high"
    assert any("mix balance" in reason for reason in audit.reasons)


def test_audit_holds_when_accepted_examples_miss_real_baseline():
    audit = build_synthetic_augmentation_audit(
        baseline_job_id="baseline",
        augmented_job_id="augmented",
        baseline_metrics={
            "val_f1": 0.81,
            "train_loss": 0.35,
            "eval_loss": 0.32,
            "confusion_matrix": _cm(7),
        },
        augmented_metrics={
            "val_f1": 0.824,
            "train_loss": 0.29,
            "eval_loss": 0.30,
            "confusion_matrix": _cm(3),
            "synthetic_train_examples": 6,
            "synthetic_candidates_seen": 8,
            "synthetic_candidates_accepted": 6,
            "synthetic_candidates_rejected": 2,
            "synthetic_accepted_real_baseline_comparison": {
                "count": 6,
                "within_real_score_range_count": 5,
                "below_real_score_range_count": 1,
                "within_real_score_range_share": 0.833,
                "candidate_score": {
                    "count": 6,
                    "avg": 0.88,
                    "min": 0.72,
                    "max": 0.95,
                },
                "baseline_min": {
                    "count": 6,
                    "avg": 0.80,
                    "min": 0.76,
                    "max": 0.84,
                },
            },
        },
        target_pairs=[("MERCHANT_NAME", "O")],
    )

    assert audit.recommendation == "hold"
    assert audit.overtraining_signal is False
    assert audit.synthetic_quality_signal is True
    assert audit.synthetic_real_baseline_signal is True
    assert audit.synthetic_accepted_real_baseline_comparison == {
        "count": 6,
        "within_real_score_range_count": 5,
        "below_real_score_range_count": 1,
        "within_real_score_range_share": 0.833,
        "candidate_score": {
            "count": 6,
            "avg": 0.88,
            "min": 0.72,
            "max": 0.95,
        },
        "baseline_min": {
            "count": 6,
            "avg": 0.80,
            "min": 0.76,
            "max": 0.84,
        },
    }
    assert any(
        "real receipt structure baseline" in reason for reason in audit.reasons
    )


def test_audit_holds_when_confusions_improve_but_overtraining_signal_rises():
    audit = build_synthetic_augmentation_audit(
        baseline_job_id="baseline",
        augmented_job_id="augmented",
        baseline_metrics={
            "val_f1": 0.81,
            "train_loss": 0.35,
            "eval_loss": 0.31,
            "confusion_matrix": _cm(7),
        },
        augmented_metrics={
            "val_f1": 0.812,
            "train_loss": 0.20,
            "eval_loss": 0.39,
            "confusion_matrix": _cm(3),
        },
        target_pairs=[("MERCHANT_NAME", "O")],
    )

    assert audit.recommendation == "hold"
    assert audit.overtraining_signal is True
    assert any("overtraining" in reason for reason in audit.reasons)


def test_audit_holds_when_synthetic_quality_gate_rejects_too_many():
    audit = build_synthetic_augmentation_audit(
        baseline_job_id="baseline",
        augmented_job_id="augmented",
        baseline_metrics={
            "val_f1": 0.81,
            "train_loss": 0.35,
            "eval_loss": 0.31,
            "confusion_matrix": _cm(7),
        },
        augmented_metrics={
            "val_f1": 0.824,
            "train_loss": 0.29,
            "eval_loss": 0.30,
            "confusion_matrix": _cm(3),
            "synthetic_train_examples": 2,
            "synthetic_candidates_seen": 10,
            "synthetic_candidates_accepted": 2,
            "synthetic_candidates_rejected": 8,
            "synthetic_rejection_reasons": {
                "low_structure_similarity": 5,
                "add_item_not_cross_receipt_grounded": 3,
            },
        },
        target_pairs=[("MERCHANT_NAME", "O")],
    )

    assert audit.recommendation == "hold"
    assert audit.overtraining_signal is False
    assert audit.synthetic_quality_signal is True
    assert audit.synthetic_acceptance_rate == pytest.approx(0.2)
    assert audit.synthetic_rejection_reasons == {
        "add_item_not_cross_receipt_grounded": 3,
        "low_structure_similarity": 5,
    }
    assert any("quality gate" in reason for reason in audit.reasons)


def test_audit_holds_when_accepted_candidate_quality_is_low():
    audit = build_synthetic_augmentation_audit(
        baseline_job_id="baseline",
        augmented_job_id="augmented",
        baseline_metrics={
            "val_f1": 0.81,
            "train_loss": 0.35,
            "eval_loss": 0.31,
            "confusion_matrix": _cm(7),
        },
        augmented_metrics={
            "val_f1": 0.824,
            "train_loss": 0.29,
            "eval_loss": 0.30,
            "confusion_matrix": _cm(3),
            "synthetic_train_examples": 48,
            "synthetic_candidates_seen": 54,
            "synthetic_candidates_accepted": 48,
            "synthetic_candidates_rejected": 6,
            "synthetic_accepted_candidate_quality": {
                "count": 48,
                "avg": 0.78,
                "min": 0.68,
                "max": 0.92,
            },
            "synthetic_accepted_candidate_quality_components": {
                "structure_similarity": {
                    "count": 48,
                    "avg": 0.82,
                    "min": 0.61,
                    "max": 0.95,
                }
            },
        },
        target_pairs=[("MERCHANT_NAME", "O")],
        min_accepted_candidate_quality=0.8,
    )

    assert audit.recommendation == "hold"
    assert audit.overtraining_signal is False
    assert audit.synthetic_quality_signal is True
    assert audit.synthetic_acceptance_rate == pytest.approx(0.8889)
    assert audit.synthetic_accepted_candidate_quality == {
        "count": 48,
        "avg": 0.78,
        "min": 0.68,
        "max": 0.92,
    }
    assert audit.synthetic_accepted_candidate_quality_components == {
        "structure_similarity": {
            "count": 48,
            "avg": 0.82,
            "min": 0.61,
            "max": 0.95,
        }
    }
    assert any("candidate quality" in reason for reason in audit.reasons)


def test_audit_rejects_when_confusions_do_not_improve_and_f1_drops():
    audit = build_synthetic_augmentation_audit(
        baseline_job_id="baseline",
        augmented_job_id="augmented",
        baseline_metrics={
            "val_f1": 0.81,
            "train_loss": 0.35,
            "eval_loss": 0.31,
            "confusion_matrix": _cm(7),
        },
        augmented_metrics={
            "val_f1": 0.79,
            "train_loss": 0.20,
            "eval_loss": 0.40,
            "confusion_matrix": _cm(8),
        },
        target_pairs=[("MERCHANT_NAME", "O")],
    )

    assert audit.recommendation == "reject"
    assert audit.overtraining_signal is True
    assert audit.total_relative_reduction < 0


def test_audit_rejects_when_no_candidates_survive_and_targets_stall():
    audit = build_synthetic_augmentation_audit(
        baseline_job_id="baseline",
        augmented_job_id="augmented",
        baseline_metrics={
            "val_f1": 0.81,
            "train_loss": 0.35,
            "eval_loss": 0.31,
            "confusion_matrix": _cm(7),
        },
        augmented_metrics={
            "val_f1": 0.811,
            "train_loss": 0.34,
            "eval_loss": 0.31,
            "confusion_matrix": _cm(7),
            "synthetic_train_examples": 0,
            "synthetic_candidates_seen": 5,
            "synthetic_candidates_accepted": 0,
            "synthetic_candidates_rejected": 5,
        },
        target_pairs=[("MERCHANT_NAME", "O")],
    )

    assert audit.recommendation == "reject"
    assert audit.overtraining_signal is False
    assert audit.synthetic_quality_signal is True
    assert audit.synthetic_acceptance_rate == 0.0


def test_resolve_job_id_from_ref_accepts_job_name_or_uuid():
    dynamo = FakeDynamoClient()

    assert (
        resolve_job_id_from_ref(dynamo, "layoutlm-augmented")
        == "22222222-2222-4222-8222-222222222222"
    )
    assert (
        resolve_job_id_from_ref(
            dynamo,
            "11111111-1111-4111-8111-111111111111",
        )
        == "11111111-1111-4111-8111-111111111111"
    )


def test_audit_from_dynamo_uses_pattern_artifact_target_pairs():
    audit = build_synthetic_augmentation_audit_from_dynamo(
        dynamo_client=FakeDynamoClient(),
        baseline_job_ref="layoutlm-baseline",
        augmented_job_ref="layoutlm-augmented",
        pattern_artifact={
            "synthetic_receipt_plan": {
                "recipes": [
                    {
                        "actual_label": "B-MERCHANT_NAME",
                        "predicted_label": "O",
                    }
                ]
            }
        },
    )

    assert audit.baseline_job_id == "11111111-1111-4111-8111-111111111111"
    assert audit.augmented_job_id == "22222222-2222-4222-8222-222222222222"
    assert audit.recommendation == "promote"
    assert audit.synthetic_train_examples == 48
    assert audit.synthetic_candidates_seen == 54
    assert audit.synthetic_candidates_accepted == 48
    assert audit.synthetic_candidates_rejected == 6
    assert audit.synthetic_quality_signal is False
    assert audit.synthetic_real_baseline_signal is False
    assert audit.synthetic_accepted_candidate_quality == {
        "count": 48,
        "avg": 0.88,
        "min": 0.79,
        "max": 0.96,
    }
    assert audit.synthetic_accepted_real_baseline_comparison == {
        "count": 48,
        "within_real_score_range_count": 48,
        "below_real_score_range_count": 0,
        "within_real_score_range_share": 1.0,
        "candidate_score": {
            "count": 48,
            "avg": 0.91,
            "min": 0.84,
            "max": 0.97,
        },
        "baseline_min": {
            "count": 48,
            "avg": 0.79,
            "min": 0.74,
            "max": 0.83,
        },
        "baseline_pair_count": {
            "count": 48,
            "avg": 7.0,
            "min": 4.0,
            "max": 12.0,
        },
    }
    assert audit.total_before_count == 14
    assert audit.total_after_count == 6
