"""Tests for optional synthetic replay launch states."""

import importlib.util
import json
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "infra"
    / "label_evaluator_step_functions"
    / "step_function_states.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "label_evaluator_step_function_states_for_test",
        MODULE_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_definition_adds_synthetic_replay_launch_when_training_lambda_exists():
    module = _load_module()

    definition = json.loads(
        module.create_step_function_definition(
            lambdas=module.LambdaArns(
                list_all_receipts="arn:list",
                unified_evaluator="arn:evaluator",
                unified_pattern_builder="arn:patterns",
                final_aggregate="arn:final",
                layoutlm_start_training="arn:start-training",
            ),
            runtime=module.RuntimeConfig(batch_bucket="pattern-bucket"),
            emr=module.EmrConfig(),
            viz_cache=module.VizCacheConfig(),
        )
    )

    states = definition["States"]
    assert states["SummarizeExecutionResults"]["Next"] == ("CheckRunSyntheticReplay")
    assert states["CheckRunSyntheticReplay"]["Choices"][0]["Next"] == (
        "StartSyntheticReplayTraining"
    )
    assert states["StartSyntheticReplayTraining"]["Resource"] == ("arn:start-training")
    assert (
        states["StartSyntheticReplayTraining"]["Parameters"]["baseline_job_ref.$"]
        == "$.config.merged.baseline_job_ref"
    )
    assert states["StartSyntheticReplayTraining"]["Parameters"][
        "synthetic_training_examples.$"
    ] == (
        "States.Format('s3://{}/line_item_patterns/{}/', "
        "$.init.batch_bucket, $.init.execution_id)"
    )
    assert (
        states["StartSyntheticReplayTraining"]["Parameters"]["instance_type.$"]
        == "$.config.merged.synthetic_replay_instance_type"
    )
    assert (
        states["StartSyntheticReplayTraining"]["Parameters"]["max_runtime_hours.$"]
        == "$.config.merged.synthetic_replay_max_runtime_hours"
    )
    assert states["SkipSyntheticReplay"]["Next"] == "CheckRunAnalytics"
    assert (
        states["SkipAnalytics"]["Parameters"]["synthetic_replay_result.$"]
        == "$.synthetic_replay_result"
    )


def test_definition_skips_synthetic_replay_states_without_training_lambda():
    module = _load_module()

    definition = json.loads(
        module.create_step_function_definition(
            lambdas=module.LambdaArns(
                list_all_receipts="arn:list",
                unified_evaluator="arn:evaluator",
                unified_pattern_builder="arn:patterns",
                final_aggregate="arn:final",
            ),
            runtime=module.RuntimeConfig(batch_bucket="pattern-bucket"),
            emr=module.EmrConfig(),
            viz_cache=module.VizCacheConfig(),
        )
    )

    states = definition["States"]
    assert states["SummarizeExecutionResults"]["Next"] == "CheckRunAnalytics"
    assert "CheckRunSyntheticReplay" not in states
    assert "StartSyntheticReplayTraining" not in states
    assert "synthetic_replay_result.$" not in states["SkipAnalytics"]["Parameters"]


def test_definition_preserves_synthetic_replay_result_with_emr_terminal_states():
    module = _load_module()

    definition = json.loads(
        module.create_step_function_definition(
            lambdas=module.LambdaArns(
                list_all_receipts="arn:list",
                unified_evaluator="arn:evaluator",
                unified_pattern_builder="arn:patterns",
                final_aggregate="arn:final",
                layoutlm_start_training="arn:start-training",
            ),
            runtime=module.RuntimeConfig(batch_bucket="pattern-bucket"),
            emr=module.EmrConfig(
                application_id="app",
                job_execution_role_arn="arn:emr-role",
                langsmith_export_bucket="langsmith-bucket",
                analytics_output_bucket="analytics-bucket",
                spark_artifacts_bucket="artifacts-bucket",
            ),
            viz_cache=module.VizCacheConfig(),
        )
    )

    states = definition["States"]
    assert (
        states["AnalyticsComplete"]["Parameters"]["synthetic_replay_result.$"]
        == "$.synthetic_replay_result"
    )
    assert (
        states["AnalyticsFailed"]["Parameters"]["synthetic_replay_result.$"]
        == "$.synthetic_replay_result"
    )
