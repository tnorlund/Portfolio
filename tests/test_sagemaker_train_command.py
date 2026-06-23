"""Tests for the SageMaker LayoutLM training entrypoint command contract."""

import importlib.util
from pathlib import Path


def _load_train_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "infra"
        / "sagemaker_training"
        / "train.py"
    )
    spec = importlib.util.spec_from_file_location(
        "sagemaker_training_entrypoint", module_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_train_command_forwards_synthetic_training_examples():
    train_module = _load_train_module()

    cmd = train_module.build_train_command(
        {
            "job_name": "layoutlm-confusion-replay",
            "dynamo_table": "ReceiptsTable",
            "synthetic_training_examples": (
                "s3://label-evaluator-batch/"
                "line_item_patterns/execution-1/merchant.json"
            ),
        }
    )

    assert cmd[:2] == ["layoutlm-cli", "train"]
    assert "--synthetic-training-examples" in cmd
    assert (
        cmd[cmd.index("--synthetic-training-examples") + 1]
        == "s3://label-evaluator-batch/line_item_patterns/execution-1/merchant.json"
    )


def test_start_training_lambda_can_promote_pattern_builder_output():
    component_source = (
        Path(__file__).resolve().parents[1]
        / "infra"
        / "sagemaker_training"
        / "component.py"
    ).read_text(encoding="utf-8")

    assert 'event.get("line_item_patterns_s3_key")' in component_source
    assert 'event.get("line_item_patterns_s3_prefix")' in component_source
    assert 'line_item_patterns_prefix = event.get("line_item_patterns_s3_prefix")' in (
        component_source
    )
    assert (
        'synthetic_training_examples = f"s3://{batch_bucket}/{line_item_patterns_prefix}"'
        in component_source
    )
    assert "line_item_patterns/{event['execution_id']}/" in component_source
    assert (
        "hyperparameters.setdefault(\n"
        '            "synthetic_training_examples", str(synthetic_training_examples)\n'
        "        )"
    ) in component_source
    assert 'event.get("synthetic_replay_cost_ack")' in component_source
    assert "synthetic replay requires 1-3 source receipts" in component_source
    assert "synthetic replay requires managed spot training" in component_source
    assert "synthetic replay is capped at one runtime hour" in component_source
    assert "synthetic replay is capped at one epoch" in component_source
    assert "ce:GetCostAndUsage" in component_source
    assert '"SYNTHETIC_REPLAY_MAX_AWS_SPEND_USD": "200"' in component_source
    assert "_require_synthetic_replay_budget(event)" in component_source
    assert "synthetic replay AWS spend cap reached" in component_source
    assert '"synthetic-augmentation", "Value": "true"' in component_source
    assert '"synthetic-replay-cost-ack", "Value": "true"' in component_source
    assert '"source-receipt-limit"' in component_source
    assert '"baseline-job-ref"' in component_source
    assert '"pattern-artifact-s3-uri"' in component_source


def test_trainer_records_synthetic_quality_gate_metrics():
    trainer_source = (
        Path(__file__).resolve().parents[1]
        / "receipt_layoutlm"
        / "receipt_layoutlm"
        / "trainer.py"
    ).read_text(encoding="utf-8")

    assert 'metric_name="synthetic_train_examples"' in trainer_source
    assert 'metric_name="synthetic_candidates_seen"' in trainer_source
    assert 'metric_name="synthetic_candidates_accepted"' in trainer_source
    assert 'metric_name="synthetic_candidates_rejected"' in trainer_source
    assert 'metric_name="synthetic_rejection_reasons"' in trainer_source
    assert 'metric_name="synthetic_accepted_operation_counts"' in trainer_source
    assert 'metric_name="synthetic_accepted_category_counts"' in trainer_source
    assert 'metric_name="synthetic_accepted_field_replacement_counts"' in trainer_source
    assert 'metric_name="synthetic_accepted_structure_similarity"' in trainer_source
    assert 'metric_name="synthetic_accepted_structure_components"' in trainer_source
    assert 'metric_name="synthetic_accepted_candidate_quality"' in trainer_source
    assert '"synthetic_accepted_candidate_quality_components"' in trainer_source
    assert 'metric_name="synthetic_accepted_mix_balance"' in trainer_source
    assert 'metric_name="synthetic_accepted_grounded_count"' in trainer_source
    assert 'metric_name="synthetic_accepted_arithmetic_count"' in trainer_source
