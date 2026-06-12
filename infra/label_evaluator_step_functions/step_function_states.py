"""
Step Function state definition helpers with typed parameters.

This module provides typed dataclasses and helper functions to build
Step Function state definitions in a clean, maintainable way.
"""

import json
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class LambdaArns:
    """ARNs for all Lambda functions used in the Step Function."""

    list_all_receipts: str
    unified_evaluator: str
    unified_pattern_builder: str
    final_aggregate: str


@dataclass
class VizCacheConfig:
    """Configuration for viz-cache Lambda (replaces LangSmith + EMR)."""

    cache_bucket: Optional[str] = None
    build_viz_cache_lambda_arn: Optional[str] = None

    @property
    def enabled(self) -> bool:
        """Check if viz-cache is configured."""
        return (
            self.cache_bucket is not None
            and self.build_viz_cache_lambda_arn is not None
        )


@dataclass
class EmrConfig:
    """Configuration for EMR Serverless analytics (optional)."""

    application_id: Optional[str] = None
    job_execution_role_arn: Optional[str] = None
    langsmith_export_bucket: Optional[str] = None
    analytics_output_bucket: Optional[str] = None
    spark_artifacts_bucket: Optional[str] = None

    @property
    def enabled(self) -> bool:
        """Check if EMR is configured."""
        return self.application_id is not None


@dataclass
class RuntimeConfig:
    """Runtime configuration for Step Function execution."""

    batch_bucket: str
    phase1_concurrency: int = 25
    phase2_concurrency: int = 40


def build_retry_config(
    error_equals: list[str],
    interval_seconds: int = 2,
    max_attempts: int = 2,
    backoff_rate: float = 2.0,
) -> dict[str, Any]:
    """Build a standard retry configuration."""
    return {
        "ErrorEquals": error_equals,
        "IntervalSeconds": interval_seconds,
        "MaxAttempts": max_attempts,
        "BackoffRate": backoff_rate,
    }


def build_llm_retry_config() -> list[dict[str, Any]]:
    """Build retry configuration for LLM tasks."""
    return [
        build_retry_config(
            ["States.Timeout"],
            interval_seconds=5,
            max_attempts=2,
            backoff_rate=1.0,
        ),
        build_retry_config(
            ["LLMRateLimitError"],
            interval_seconds=30,
            max_attempts=5,
            backoff_rate=2.0,
        ),
        build_retry_config(
            ["States.TaskFailed"],
            interval_seconds=2,
            max_attempts=2,
            backoff_rate=2.0,
        ),
    ]


def build_input_normalization_states() -> dict[str, Any]:
    """Build states for normalizing input and setting defaults."""
    return {
        "NormalizeInput": {
            "Type": "Pass",
            "Parameters": {
                "original_input.$": "$",
                "merged_input.$": "$",
            },
            "ResultPath": "$.normalized",
            "Next": "SetDefaults",
        },
        "SetDefaults": {
            "Type": "Pass",
            "Parameters": {
                "limit": None,
                "since_date": None,
                "langchain_project.$": (
                    "States.Format('label-eval-{}', $$.Execution.StartTime)"
                ),
                "run_analytics": True,
            },
            "ResultPath": "$.defaults",
            "Next": "MergeInputWithDefaults",
        },
        "MergeInputWithDefaults": {
            "Type": "Pass",
            "Parameters": {
                "merged.$": (
                    "States.JsonMerge("
                    "$.defaults, "
                    "$.normalized.merged_input, "
                    "false)"
                ),
            },
            "ResultPath": "$.config",
            "Next": "Initialize",
        },
    }


def build_input_mode_states(
    batch_bucket: str,
) -> dict[str, Any]:
    """Build initialization state."""
    return {
        "Initialize": {
            "Type": "Pass",
            "Parameters": {
                "execution_id.$": "$$.Execution.Name",
                "start_time.$": "$$.Execution.StartTime",
                "batch_bucket": batch_bucket,
                "langchain_project.$": "$.config.merged.langchain_project",
                "run_analytics.$": "$.config.merged.run_analytics",
                "max_training_receipts": 50,
                "min_receipts": 5,
                "limit.$": "$.config.merged.limit",
                "since_date.$": "$.config.merged.since_date",
                "original_input.$": "$.normalized.original_input",
            },
            "ResultPath": "$.init",
            "Next": "ListAllReceipts",
        },
    }


def build_list_receipts_states(list_all_receipts_arn: str) -> dict[str, Any]:
    """Build states for listing receipts.

    Flow:
        HasReceipts (total_receipts > 0)
            ├─ YES → HasMerchants (total_merchants > 0)
            │           ├─ YES → ComputeAllPatterns → ProcessReceipts
            │           └─ NO → SkipPatterns → ProcessReceipts
            └─ NO → NoReceipts (end)
    """
    retry_config = [build_retry_config(["States.TaskFailed"])]

    return {
        "ListAllReceipts": {
            "Type": "Task",
            "Resource": list_all_receipts_arn,
            "TimeoutSeconds": 300,
            "Parameters": {
                "execution_id.$": "$.init.execution_id",
                "batch_bucket.$": "$.init.batch_bucket",
                "min_receipts.$": "$.init.min_receipts",
                "max_training_receipts.$": "$.init.max_training_receipts",
                "limit.$": "$.init.limit",
                "since_date.$": "$.init.since_date",
            },
            "ResultPath": "$.all_data",
            "Retry": retry_config,
            "Next": "HasReceipts",
        },
        "HasReceipts": {
            "Type": "Choice",
            "Choices": [
                {
                    "Variable": "$.all_data.total_receipts",
                    "NumericGreaterThan": 0,
                    "Next": "HasMerchants",
                }
            ],
            "Default": "NoReceipts",
        },
        "HasMerchants": {
            "Type": "Choice",
            "Choices": [
                {
                    "Variable": "$.all_data.total_merchants",
                    "NumericGreaterThan": 0,
                    "Next": "ComputeAllPatterns",
                }
            ],
            "Default": "SkipPatterns",
        },
        "SkipPatterns": {
            "Type": "Pass",
            "Comment": "No merchants qualify for patterns, skip to receipt processing",
            "Result": [],
            "ResultPath": "$.pattern_results",
            "Next": "ProcessReceipts",
        },
        "NoReceipts": {
            "Type": "Pass",
            "Result": {"message": "No receipts found"},
            "End": True,
        },
    }


def build_pattern_computation_states(
    unified_pattern_builder_arn: str,
    phase1_concurrency: int,
) -> dict[str, Any]:
    """Build Phase 1 pattern computation states using unified pattern builder.

    The unified pattern builder combines LearnLineItemPatterns (LLM discovery)
    and BuildMerchantPatterns (geometric computation) into a single Lambda.
    """
    retry_config = [
        build_retry_config(
            ["States.TaskFailed"],
            interval_seconds=5,
        ),
        build_retry_config(
            ["LLMRateLimitError"],
            interval_seconds=30,
            max_attempts=5,
            backoff_rate=2.0,
        ),
    ]

    return {
        "ComputeAllPatterns": {
            "Type": "Map",
            "ItemsPath": "$.all_data.merchants",
            "MaxConcurrency": phase1_concurrency,
            "Parameters": {
                "merchant.$": "$$.Map.Item.Value",
                "execution_id.$": "$.init.execution_id",
                "batch_bucket.$": "$.init.batch_bucket",
                "max_training_receipts.$": "$.init.max_training_receipts",
                "langchain_project.$": "$.init.langchain_project",
            },
            "ItemProcessor": {
                "ProcessorConfig": {"Mode": "INLINE"},
                "StartAt": "UnifiedPatternBuilder",
                "States": {
                    "UnifiedPatternBuilder": {
                        "Type": "Task",
                        "Resource": unified_pattern_builder_arn,
                        "TimeoutSeconds": 900,  # 15 minutes (Lambda max)
                        "Parameters": {
                            "execution_id.$": "$.execution_id",
                            "batch_bucket.$": "$.batch_bucket",
                            "merchant_name.$": "$.merchant.merchant_name",
                            "max_training_receipts.$": "$.max_training_receipts",
                            "langchain_project.$": "$.langchain_project",
                            "execution_arn.$": "$$.Execution.Id",
                        },
                        "ResultPath": "$.pattern_result",
                        "Retry": retry_config,
                        "Next": "ReturnPatternResult",
                    },
                    "ReturnPatternResult": {
                        "Type": "Pass",
                        "Parameters": {
                            "merchant_name.$": "$.merchant.merchant_name",
                            "patterns_s3_key.$": "$.pattern_result.patterns_s3_key",
                            "line_item_patterns_s3_key.$": (
                                "$.pattern_result.line_item_patterns_s3_key"
                            ),
                            "receipt_count.$": "$.pattern_result.receipt_count",
                            "status": "patterns_computed",
                        },
                        "End": True,
                    },
                },
            },
            "ResultPath": "$.pattern_results",
            "Next": "ProcessReceipts",
        },
    }


def build_receipt_processing_states(
    lambdas: LambdaArns,
    phase2_concurrency: int,
) -> dict[str, Any]:
    """Build Phase 2 receipt processing states using unified evaluator.

    The unified evaluator now handles:
    1. Fetching receipt data directly from DynamoDB
    2. Writing receipt data to S3 for the EMR job
    3. Running all evaluation phases
    4. Writing results to S3

    This eliminates the previous LoadReceiptData step, reducing Lambda
    invocations and S3 round-trips.
    """
    # Single Map over all receipts with MaxConcurrency
    return {
        "ProcessReceipts": {
            "Type": "Map",
            "ItemsPath": "$.all_data.receipts",
            "MaxConcurrency": phase2_concurrency,
            "Parameters": {
                "receipt.$": "$$.Map.Item.Value",
                "receipt_index.$": "$$.Map.Item.Index",
                "execution_id.$": "$.init.execution_id",
                "batch_bucket.$": "$.init.batch_bucket",
                "langchain_project.$": "$.init.langchain_project",
            },
            "ItemProcessor": {
                "ProcessorConfig": {"Mode": "INLINE"},
                "StartAt": "UnifiedReceiptEvaluator",
                "States": {
                    "UnifiedReceiptEvaluator": {
                        "Type": "Task",
                        "Resource": lambdas.unified_evaluator,
                        "TimeoutSeconds": 900,  # 15 minutes for all evaluations
                        "Parameters": {
                            # Pass receipt object directly (new format)
                            # Lambda will fetch from DynamoDB and write to S3
                            "receipt.$": "$.receipt",
                            "execution_id.$": "$.execution_id",
                            "batch_bucket.$": "$.batch_bucket",
                            "langchain_project.$": "$.langchain_project",
                            "execution_arn.$": "$$.Execution.Id",
                        },
                        "ResultPath": "$.evaluation_result",
                        "Retry": build_llm_retry_config(),
                        "Catch": [
                            {
                                "ErrorEquals": ["States.ALL"],
                                "ResultPath": "$.evaluation_error",
                                "Next": "EvaluationFailed",
                            }
                        ],
                        "Next": "ReturnResult",
                    },
                    "EvaluationFailed": {
                        "Type": "Pass",
                        "Parameters": {
                            "status": "failed",
                            "image_id.$": "$.receipt.image_id",
                            "receipt_id.$": "$.receipt.receipt_id",
                            "merchant_name.$": "$.receipt.merchant_name",
                            "issues_found": 0,
                            "error.$": "$.evaluation_error.Error",
                            "cause.$": "$.evaluation_error.Cause",
                        },
                        "End": True,
                    },
                    "ReturnResult": {
                        "Type": "Pass",
                        "Parameters": {
                            "status.$": "$.evaluation_result.status",
                            "image_id.$": "$.evaluation_result.image_id",
                            "receipt_id.$": "$.evaluation_result.receipt_id",
                            "merchant_name.$": "$.receipt.merchant_name",
                            "issues_found.$": "$.evaluation_result.issues_found",
                        },
                        "End": True,
                    },
                },
            },
            "ResultPath": "$.receipt_results",
            "Next": "SummarizeExecutionResults",
        },
    }


def build_summarize_states(
    final_aggregate_arn: str,
    *,
    viz_cache_enabled: bool = False,
) -> dict[str, Any]:
    """Build states for summarizing execution results."""
    next_state = "BuildVizCache" if viz_cache_enabled else "CheckRunAnalytics"
    return {
        "SummarizeExecutionResults": {
            "Type": "Task",
            "Resource": final_aggregate_arn,
            "TimeoutSeconds": 300,
            "Parameters": {
                "execution_id.$": "$.init.execution_id",
                "batch_bucket.$": "$.init.batch_bucket",
                "receipt_results.$": "$.receipt_results",
                "pattern_results.$": "$.pattern_results",
                "total_merchants.$": "$.all_data.total_merchants",
                "total_receipts.$": "$.all_data.total_receipts",
            },
            "ResultPath": "$.summary_result",
            "Next": next_state,
        },
    }


def build_viz_cache_states(viz_cache: VizCacheConfig) -> dict[str, Any]:
    """Build BuildVizCache state that writes cache directly via Lambda."""
    if not viz_cache.enabled:
        return {}

    return {
        "BuildVizCache": {
            "Type": "Task",
            "Resource": viz_cache.build_viz_cache_lambda_arn,
            "TimeoutSeconds": 300,
            "Parameters": {
                "execution_id.$": "$.summary_result.execution_id",
                "batch_bucket.$": "$.init.batch_bucket",
                "cache_bucket": viz_cache.cache_bucket,
                "total_receipts.$": "$.summary_result.total_receipts",
            },
            "ResultPath": "$.viz_cache_result",
            "Retry": [
                build_retry_config(
                    ["States.TaskFailed"],
                    interval_seconds=5,
                )
            ],
            "Next": "CheckRunAnalytics",
        },
    }


def build_analytics_decision_states(
    emr_enabled: bool,
) -> dict[str, Any]:
    """Build states for deciding whether to run analytics.

    Args:
        emr_enabled: Whether EMR analytics is enabled.
    """
    emr_next = "CheckEMREnabled" if emr_enabled else "SkipAnalytics"
    run_next = "RunSparkAnalytics" if emr_enabled else "SkipAnalytics"

    states: dict[str, Any] = {
        "CheckRunAnalytics": {
            "Type": "Choice",
            "Choices": [
                {
                    "Variable": "$.init.run_analytics",
                    "BooleanEquals": False,
                    "Next": "SkipAnalytics",
                }
            ],
            "Default": emr_next,
        },
        "SkipAnalytics": {
            "Type": "Pass",
            "Parameters": {
                "status.$": "$.summary_result.status",
                "execution_id.$": "$.summary_result.execution_id",
                "total_merchants.$": "$.summary_result.total_merchants",
                "total_receipts.$": "$.summary_result.total_receipts",
                "total_issues.$": "$.summary_result.total_issues",
                "analytics_status": "skipped",
                "langchain_project.$": "$.init.langchain_project",
            },
            "End": True,
        },
    }

    if emr_enabled:
        states["CheckEMREnabled"] = {
            "Type": "Choice",
            "Choices": [
                {
                    "Variable": "$.summary_result.status",
                    "StringEquals": "completed",
                    "Next": run_next,
                }
            ],
            "Default": "SkipAnalytics",
        }

    return states


def build_emr_states(emr: EmrConfig) -> dict[str, Any]:
    """Build EMR Serverless analytics states if EMR is configured."""
    if not emr.enabled:
        return {}

    # Build SparkSubmitParameters - uses Python from custom Docker image
    artifacts_bucket = emr.spark_artifacts_bucket
    spark_submit_params = (
        "--conf spark.sql.legacy.parquet.nanosAsLong=true "
        "--conf spark.sql.adaptive.enabled=true "
        "--conf spark.sql.shuffle.partitions=32 "
        "--conf spark.sql.adaptive.coalescePartitions.initialPartitionNum=32 "
        "--conf spark.sql.files.openCostInBytes=134217728 "
        "--conf spark.sql.files.maxPartitionBytes=268435456 "
        "--conf spark.sql.parquet.enableVectorizedReader=false "
        "--conf spark.dynamicAllocation.enabled=false "
        "--conf spark.executor.cores=2 "
        "--conf spark.executor.memory=4g "
        "--conf spark.executor.instances=2 "
        "--conf spark.driver.cores=2 "
        "--conf spark.driver.memory=4g"
    )

    output_bucket = emr.analytics_output_bucket
    langsmith_bucket = emr.langsmith_export_bucket

    states: dict[str, Any] = {}

    # Analytics only (no viz-cache - that's now handled by BuildVizCache Lambda)
    entry_point = f"s3://{artifacts_bucket}/spark/merged_job.py"
    entry_args_expr = (
        f"States.Array("
        f"'--job-type', 'analytics', "
        f"'--parquet-input', 's3://{langsmith_bucket}/traces/', "
        f"'--analytics-output', States.Format('s3://{output_bucket}/analytics/{{}}', "
        f"$.summary_result.execution_id), "
        f"'--partition-by-merchant')"
    )

    states["RunSparkAnalytics"] = {
        "Type": "Task",
        "Resource": "arn:aws:states:::emr-serverless:startJobRun.sync",
        "Parameters": {
            "ApplicationId": emr.application_id,
            "ExecutionRoleArn": emr.job_execution_role_arn,
            "Name.$": (
                "States.Format('analytics-{}', "
                "$.summary_result.execution_id)"
            ),
            "JobDriver": {
                "SparkSubmit": {
                    "EntryPoint": entry_point,
                    "EntryPointArguments.$": entry_args_expr,
                    "SparkSubmitParameters": spark_submit_params,
                }
            },
            "ConfigurationOverrides": {
                "MonitoringConfiguration": {
                    "S3MonitoringConfiguration": {
                        "LogUri": (
                            f"s3://{emr.analytics_output_bucket}/logs/"
                        ),
                    }
                }
            },
        },
        "ResultPath": "$.spark_result",
        "TimeoutSeconds": 1800,
        "Retry": [
            build_retry_config(
                ["States.TaskFailed"],
                interval_seconds=30,
            )
        ],
        "Catch": [
            {
                "ErrorEquals": ["States.ALL"],
                "ResultPath": "$.spark_error",
                "Next": "AnalyticsFailed",
            }
        ],
        "Next": "AnalyticsComplete",
    }

    states["AnalyticsComplete"] = {
        "Type": "Pass",
        "Parameters": {
            "status.$": "$.summary_result.status",
            "execution_id.$": "$.summary_result.execution_id",
            "total_merchants.$": "$.summary_result.total_merchants",
            "total_receipts.$": "$.summary_result.total_receipts",
            "total_issues.$": "$.summary_result.total_issues",
            "analytics_status": "completed",
            "analytics_job_id.$": "$.spark_result.JobRunId",
            "analytics_output": (
                f"s3://{emr.analytics_output_bucket}/analytics/"
            ),
            "langchain_project.$": "$.init.langchain_project",
        },
        "End": True,
    }

    states["AnalyticsFailed"] = {
        "Type": "Pass",
        "Parameters": {
            "status.$": "$.summary_result.status",
            "execution_id.$": "$.summary_result.execution_id",
            "total_merchants.$": "$.summary_result.total_merchants",
            "total_receipts.$": "$.summary_result.total_receipts",
            "total_issues.$": "$.summary_result.total_issues",
            "analytics_status": "failed",
            "analytics_error.$": "$.spark_error.Error",
            "langchain_project.$": "$.init.langchain_project",
        },
        "End": True,
    }

    return states


def create_step_function_definition(
    lambdas: LambdaArns,
    runtime: RuntimeConfig,
    emr: EmrConfig,
    viz_cache: VizCacheConfig | None = None,
) -> str:
    """
    Create Step Function definition with two-phase flattened architecture.

    TWO-PHASE ARCHITECTURE:
    Phase 1: Compute all merchant patterns in parallel (MaxConcurrency)
    Phase 2: Process all receipts in parallel (single Map with MaxConcurrency)

    Both phases use simple Map states with MaxConcurrency - no nested batching.

    Args:
        lambdas: Lambda function ARNs
        runtime: Runtime configuration (bucket, concurrency, etc.)
        emr: EMR Serverless configuration (optional)
        viz_cache: Viz-cache configuration (optional, replaces LangSmith+EMR)

    Returns:
        JSON string of the Step Function definition
    """
    vc_enabled = viz_cache.enabled if viz_cache else False

    # Build all state groups
    states: dict[str, Any] = {}

    # Input normalization and defaults
    states.update(build_input_normalization_states())

    # Input mode checking and initialization
    states.update(
        build_input_mode_states(
            runtime.batch_bucket,
        )
    )

    # List receipts (single merchant and all merchants)
    states.update(build_list_receipts_states(lambdas.list_all_receipts))

    # Phase 1: Pattern computation (unified pattern builder)
    states.update(
        build_pattern_computation_states(
            lambdas.unified_pattern_builder,
            runtime.phase1_concurrency,
        )
    )

    # Phase 2: Receipt processing
    states.update(
        build_receipt_processing_states(
            lambdas,
            runtime.phase2_concurrency,
        )
    )

    # Summarize results
    states.update(
        build_summarize_states(
            lambdas.final_aggregate,
            viz_cache_enabled=vc_enabled,
        )
    )

    # Viz-cache states (if enabled — BuildVizCache Lambda)
    if viz_cache:
        states.update(build_viz_cache_states(viz_cache))

    # Analytics decision states
    states.update(build_analytics_decision_states(emr.enabled))

    # EMR states (if enabled)
    states.update(build_emr_states(emr))

    # Build final definition
    definition = {
        "Comment": (
            f"Label Evaluator Two-Phase "
            f"(Phase1={runtime.phase1_concurrency}, "
            f"Phase2={runtime.phase2_concurrency})"
        ),
        "StartAt": "NormalizeInput",
        "States": states,
    }

    return json.dumps(definition)
