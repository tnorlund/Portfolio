"""
Step Function state definition helpers with typed parameters.

This module provides typed dataclasses and helper functions to build
Step Function state definitions in a clean, maintainable way.
"""

import json
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class LambdaArns:  # pylint: disable=too-many-instance-attributes
    """ARNs for all Lambda functions used in the Step Function."""

    list_merchants: str
    list_receipts: str
    list_all_receipts: str
    fetch_receipt_data: str
    compute_patterns: str
    evaluate_labels: str
    evaluate_currency: str
    evaluate_metadata: str
    evaluate_financial: str
    close_trace: str
    aggregate_results: str
    final_aggregate: str
    discover_patterns: str
    llm_review: str
    unified_evaluator: str


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
    max_concurrency: int = 8
    batch_size: int = 10
    phase1_concurrency: int = 25
    phase2_concurrency: int = 16


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
            ["OllamaRateLimitError"],
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
            "Result": {
                "dry_run": False,
                "force_rediscovery": False,
                "enable_tracing": True,
                "limit": None,
                "langchain_project": None,
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
            "Next": "CheckInputMode",
        },
    }


def build_input_mode_states(
    batch_bucket: str,
    batch_size: int,
) -> dict[str, Any]:
    """Build states for checking input mode and initialization."""
    return {
        "CheckInputMode": {
            "Type": "Choice",
            "Choices": [
                {
                    "Variable": "$.config.merged.merchant_name",
                    "IsPresent": True,
                    "Next": "InitializeSingleMerchant",
                }
            ],
            "Default": "InitializeAllMerchants",
        },
        "InitializeSingleMerchant": {
            "Type": "Pass",
            "Parameters": {
                "execution_id.$": "$$.Execution.Name",
                "start_time.$": "$$.Execution.StartTime",
                "batch_bucket": batch_bucket,
                "batch_size": batch_size,
                "merchant_name.$": "$.config.merged.merchant_name",
                "dry_run.$": "$.config.merged.dry_run",
                "force_rediscovery.$": "$.config.merged.force_rediscovery",
                "enable_tracing.$": "$.config.merged.enable_tracing",
                "langchain_project.$": "$.config.merged.langchain_project",
                "run_analytics.$": "$.config.merged.run_analytics",
                "max_training_receipts": 50,
                "min_receipts": 5,
                "limit.$": "$.config.merged.limit",
                "original_input.$": "$.normalized.original_input",
            },
            "ResultPath": "$.init",
            "Next": "ListAllReceiptsSingleMerchant",
        },
        "InitializeAllMerchants": {
            "Type": "Pass",
            "Parameters": {
                "execution_id.$": "$$.Execution.Name",
                "start_time.$": "$$.Execution.StartTime",
                "batch_bucket": batch_bucket,
                "batch_size": batch_size,
                "merchant_name": None,
                "dry_run.$": "$.config.merged.dry_run",
                "force_rediscovery.$": "$.config.merged.force_rediscovery",
                "enable_tracing.$": "$.config.merged.enable_tracing",
                "langchain_project.$": "$.config.merged.langchain_project",
                "run_analytics.$": "$.config.merged.run_analytics",
                "max_training_receipts": 50,
                "min_receipts": 5,
                "limit.$": "$.config.merged.limit",
                "original_input.$": "$.normalized.original_input",
            },
            "ResultPath": "$.init",
            "Next": "ListAllReceipts",
        },
    }


def build_list_receipts_states(list_all_receipts_arn: str) -> dict[str, Any]:
    """Build states for listing receipts (single and all merchants)."""
    retry_config = [build_retry_config(["States.TaskFailed"])]

    return {
        "ListAllReceiptsSingleMerchant": {
            "Type": "Task",
            "Resource": list_all_receipts_arn,
            "TimeoutSeconds": 300,
            "Parameters": {
                "execution_id.$": "$.init.execution_id",
                "batch_bucket.$": "$.init.batch_bucket",
                "batch_size.$": "$.init.batch_size",
                "min_receipts.$": "$.init.min_receipts",
                "max_training_receipts.$": "$.init.max_training_receipts",
                "limit.$": "$.init.limit",
                "merchants": [{"merchant_name.$": "$.init.merchant_name"}],
            },
            "ResultPath": "$.all_data",
            "Retry": retry_config,
            "Next": "HasMerchants",
        },
        "ListAllReceipts": {
            "Type": "Task",
            "Resource": list_all_receipts_arn,
            "TimeoutSeconds": 300,
            "Parameters": {
                "execution_id.$": "$.init.execution_id",
                "batch_bucket.$": "$.init.batch_bucket",
                "batch_size.$": "$.init.batch_size",
                "min_receipts.$": "$.init.min_receipts",
                "max_training_receipts.$": "$.init.max_training_receipts",
                "limit.$": "$.init.limit",
            },
            "ResultPath": "$.all_data",
            "Retry": retry_config,
            "Next": "HasMerchants",
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
            "Default": "NoMerchants",
        },
        "NoMerchants": {
            "Type": "Pass",
            "Result": {"message": "No merchants found"},
            "End": True,
        },
    }


def build_pattern_computation_states(
    discover_patterns_arn: str,
    compute_patterns_arn: str,
    phase1_concurrency: int,
) -> dict[str, Any]:
    """Build Phase 1 pattern computation states."""
    retry_config = [
        build_retry_config(
            ["States.TaskFailed"],
            interval_seconds=5,
        )
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
                "force_rediscovery.$": "$.init.force_rediscovery",
                "enable_tracing.$": "$.init.enable_tracing",
                "langchain_project.$": "$.init.langchain_project",
            },
            "ItemProcessor": {
                "ProcessorConfig": {"Mode": "INLINE"},
                "StartAt": "LearnLineItemPatterns",
                "States": {
                    "LearnLineItemPatterns": {
                        "Type": "Task",
                        "Resource": discover_patterns_arn,
                        "TimeoutSeconds": 600,
                        "Parameters": {
                            "execution_id.$": "$.execution_id",
                            "batch_bucket.$": "$.batch_bucket",
                            "merchant_name.$": "$.merchant.merchant_name",
                            "force_rediscovery.$": "$.force_rediscovery",
                            "enable_tracing.$": "$.enable_tracing",
                            "langchain_project.$": "$.langchain_project",
                            "execution_arn.$": "$$.Execution.Id",
                        },
                        "ResultPath": "$.line_item_patterns",
                        "Retry": retry_config,
                        "Next": "BuildMerchantPatterns",
                    },
                    "BuildMerchantPatterns": {
                        "Type": "Task",
                        "Resource": compute_patterns_arn,
                        "TimeoutSeconds": 600,
                        "Parameters": {
                            "execution_id.$": "$.execution_id",
                            "batch_bucket.$": "$.batch_bucket",
                            "merchant.$": "$.merchant",
                            "max_training_receipts.$": (
                                "$.max_training_receipts"
                            ),
                            "langchain_project.$": "$.langchain_project",
                            "execution_arn.$": "$$.Execution.Id",
                        },
                        "ResultPath": "$.patterns_result",
                        "Retry": retry_config,
                        "Next": "ReturnPatternResult",
                    },
                    "ReturnPatternResult": {
                        "Type": "Pass",
                        "Parameters": {
                            "merchant_name.$": "$.merchant.merchant_name",
                            "patterns_s3_key.$": (
                                "$.patterns_result.patterns_s3_key"
                            ),
                            "line_item_patterns_s3_key.$": (
                                "$.line_item_patterns.patterns_s3_key"
                            ),
                            "status": "patterns_computed",
                        },
                        "End": True,
                    },
                },
            },
            "ResultPath": "$.pattern_results",
            "Next": "ProcessReceiptBatches",
        },
    }


def build_parallel_review_branch(
    _branch_name: str,
    state_name: str,
    resource_arn: str,
    timeout_seconds: int,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Build a single branch for parallel review."""
    # _branch_name reserved for future use (e.g., logging/debugging)
    return {
        "StartAt": state_name,
        "States": {
            state_name: {
                "Type": "Task",
                "Resource": resource_arn,
                "TimeoutSeconds": timeout_seconds,
                "Parameters": parameters,
                "Retry": build_llm_retry_config(),
                "End": True,
            },
        },
    }


def build_receipt_processing_states(
    lambdas: LambdaArns,
    phase2_concurrency: int,
) -> dict[str, Any]:
    """Build Phase 2 receipt processing states using unified evaluator."""
    # Simplified receipt processor using unified handler
    receipt_processor = {
        "ProcessorConfig": {"Mode": "INLINE"},
        "StartAt": "LoadReceiptData",
        "States": {
            "LoadReceiptData": {
                "Type": "Task",
                "Resource": lambdas.fetch_receipt_data,
                "TimeoutSeconds": 60,
                "Parameters": {
                    "receipt.$": "$.receipt",
                    "execution_id.$": "$.execution_id",
                    "batch_bucket.$": "$.batch_bucket",
                    "execution_arn.$": "$$.Execution.Id",
                },
                "ResultPath": "$.receipt_data",
                "Retry": [
                    build_retry_config(
                        ["States.TaskFailed"],
                        interval_seconds=1,
                    )
                ],
                "Next": "UnifiedReceiptEvaluator",
            },
            "UnifiedReceiptEvaluator": {
                "Type": "Task",
                "Resource": lambdas.unified_evaluator,
                "TimeoutSeconds": 900,  # 15 minutes to cover all evaluations
                "Parameters": {
                    "data_s3_key.$": "$.receipt_data.data_s3_key",
                    "merchant_name.$": "$.receipt.merchant_name",
                    "execution_id.$": "$.execution_id",
                    "batch_bucket.$": "$.batch_bucket",
                    "dry_run.$": "$.dry_run",
                    "enable_tracing.$": "$.enable_tracing",
                    "langchain_project.$": "$.langchain_project",
                    "receipt_trace_id.$": "$.receipt_data.receipt_trace_id",
                    "execution_arn.$": "$$.Execution.Id",
                },
                "ResultPath": "$.evaluation_result",
                "Retry": build_llm_retry_config(),
                "Next": "ReturnResult",
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
    }

    return {
        "ProcessReceiptBatches": {
            "Type": "Map",
            "ItemsPath": "$.all_data.receipt_batches",
            "MaxConcurrency": 0,
            "Parameters": {
                "batch.$": "$$.Map.Item.Value",
                "batch_index.$": "$$.Map.Item.Index",
                "execution_id.$": "$.init.execution_id",
                "batch_bucket.$": "$.init.batch_bucket",
                "dry_run.$": "$.init.dry_run",
                "enable_tracing.$": "$.init.enable_tracing",
                "langchain_project.$": "$.init.langchain_project",
            },
            "ItemProcessor": {
                "ProcessorConfig": {"Mode": "INLINE"},
                "StartAt": "ProcessReceipts",
                "States": {
                    "ProcessReceipts": {
                        "Type": "Map",
                        "ItemsPath": "$.batch",
                        "MaxConcurrency": phase2_concurrency,
                        "Parameters": {
                            "receipt.$": "$$.Map.Item.Value",
                            "receipt_index.$": "$$.Map.Item.Index",
                            "batch_index.$": "$.batch_index",
                            "execution_id.$": "$.execution_id",
                            "batch_bucket.$": "$.batch_bucket",
                            "dry_run.$": "$.dry_run",
                            "enable_tracing.$": "$.enable_tracing",
                            "langchain_project.$": "$.langchain_project",
                        },
                        "ItemProcessor": receipt_processor,
                        "End": True,
                    },
                },
            },
            "ResultPath": "$.batch_results",
            "Next": "SummarizeExecutionResults",
        },
    }


def build_summarize_states(final_aggregate_arn: str) -> dict[str, Any]:
    """Build states for summarizing execution results."""
    return {
        "SummarizeExecutionResults": {
            "Type": "Task",
            "Resource": final_aggregate_arn,
            "TimeoutSeconds": 300,
            "Parameters": {
                "execution_id.$": "$.init.execution_id",
                "batch_bucket.$": "$.init.batch_bucket",
                "all_batch_results.$": "$.batch_results",
                "pattern_results.$": "$.pattern_results",
                "total_merchants.$": "$.all_data.total_merchants",
                "total_receipts.$": "$.all_data.total_receipts",
            },
            "ResultPath": "$.summary_result",
            "Next": "CheckRunAnalytics",
        },
    }


def build_analytics_decision_states(emr_enabled: bool) -> dict[str, Any]:
    """Build states for deciding whether to run analytics."""
    emr_next = "CheckEMREnabled" if emr_enabled else "SkipAnalytics"
    run_next = "RunSparkAnalytics" if emr_enabled else "SkipAnalytics"

    return {
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
        "CheckEMREnabled": {
            "Type": "Choice",
            "Choices": [
                {
                    "Variable": "$.summary_result.status",
                    "StringEquals": "completed",
                    "Next": run_next,
                }
            ],
            "Default": "SkipAnalytics",
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


def build_emr_states(emr: EmrConfig) -> dict[str, Any]:
    """Build EMR Serverless analytics states if EMR is configured."""
    if not emr.enabled:
        return {}

    # Build SparkSubmitParameters as multi-line for readability
    artifacts_bucket = emr.spark_artifacts_bucket
    spark_submit_params = (
        f"--conf spark.archives="
        f"s3://{artifacts_bucket}/spark/spark_env.tar.gz#environment "
        "--conf spark.emr-serverless.driverEnv.PYSPARK_DRIVER_PYTHON="
        "./environment/bin/python "
        "--conf spark.emr-serverless.driverEnv.PYSPARK_PYTHON="
        "./environment/bin/python "
        "--conf spark.executorEnv.PYSPARK_PYTHON="
        "./environment/bin/python "
        "--conf spark.executor.cores=2 "
        "--conf spark.executor.memory=4g "
        "--conf spark.driver.cores=2 "
        "--conf spark.driver.memory=4g"
    )

    # Build entry point arguments with proper formatting
    output_bucket = emr.analytics_output_bucket
    entry_args = [
        "--input",
        f"s3://{emr.langsmith_export_bucket}/traces/",
        "--output.$",
        f"States.Format('s3://{output_bucket}/analytics/{{}}', "
        "$.summary_result.execution_id)",
        "--job-type",
        "all",
        "--partition-by-merchant",
    ]

    return {
        "RunSparkAnalytics": {
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
                        "EntryPoint": (
                            f"s3://{artifacts_bucket}/spark/emr_job.py"
                        ),
                        "EntryPointArguments": entry_args,
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
        },
        "AnalyticsComplete": {
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
        },
        "AnalyticsFailed": {
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
        },
    }


def create_step_function_definition(
    lambdas: LambdaArns,
    runtime: RuntimeConfig,
    emr: EmrConfig,
) -> str:
    """
    Create Step Function definition with two-phase flattened architecture.

    TWO-PHASE ARCHITECTURE:
    Phase 1: Compute all merchant patterns in parallel (25 concurrent)
    Phase 2: Process all receipts in parallel (16 concurrent per batch)

    This eliminates the nested Map bottleneck where small merchants wait
    for large merchants to finish pattern computation.

    Args:
        lambdas: Lambda function ARNs
        runtime: Runtime configuration (bucket, concurrency, etc.)
        emr: EMR Serverless configuration (optional)

    Returns:
        JSON string of the Step Function definition
    """
    # Build all state groups
    states: dict[str, Any] = {}

    # Input normalization and defaults
    states.update(build_input_normalization_states())

    # Input mode checking and initialization
    states.update(
        build_input_mode_states(
            runtime.batch_bucket,
            runtime.batch_size,
        )
    )

    # List receipts (single merchant and all merchants)
    states.update(build_list_receipts_states(lambdas.list_all_receipts))

    # Phase 1: Pattern computation
    states.update(
        build_pattern_computation_states(
            lambdas.discover_patterns,
            lambdas.compute_patterns,
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
    states.update(build_summarize_states(lambdas.final_aggregate))

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
