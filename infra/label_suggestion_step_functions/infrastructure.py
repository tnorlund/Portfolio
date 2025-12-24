"""
Pulumi infrastructure for Label Suggestion Step Function.

This component creates a Step Function that:
1. Prepares receipts with unlabeled words
2. Processes receipts in batches (container Lambda with ChromaDB + LLM)
3. Aggregates results and generates reports

Architecture:
- Zip Lambda: prepare_receipts (DynamoDB layer only, fast startup)
- Container Lambda: suggest_labels (ChromaDB + LLM, heavy processing)
- S3 Bucket: batch files and results storage
- Step Function: orchestration with parallel execution
"""

import json
import os
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import (
    AssetArchive,
    ComponentResource,
    Config,
    FileAsset,
    Output,
    ResourceOptions,
)
from pulumi_aws.cloudwatch import LogGroup
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment
from pulumi_aws.lambda_ import Function, FunctionEnvironmentArgs
from pulumi_aws.s3 import (
    Bucket,
    BucketVersioning,
    BucketVersioningVersioningConfigurationArgs,
)
from pulumi_aws.sfn import StateMachine, StateMachineLoggingConfigurationArgs

# Import shared components
try:
    from codebuild_docker_image import CodeBuildDockerImage
    from dynamo_db import dynamodb_table
    from lambda_layer import dynamo_layer
except ImportError:
    # For type checking / IDE support
    CodeBuildDockerImage = None  # type: ignore
    dynamodb_table = None  # type: ignore
    dynamo_layer = None  # type: ignore

# Load secrets
config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")
ollama_api_key = config.require_secret("OLLAMA_API_KEY")
langchain_api_key = config.require_secret("LANGCHAIN_API_KEY")

# Label suggestion specific config
suggestion_config = Config("label-suggestion")
max_concurrency_process_default = (
    suggestion_config.get_int("max_concurrency_process") or 5
)  # More permissive than validation


class LabelSuggestionStepFunction(ComponentResource):
    """
    Step Function infrastructure for label suggestion.

    Components:
    - Zip Lambda: prepare_receipts (DynamoDB layer only, fast)
    - Container Lambda: suggest_labels (ChromaDB + LLM)
    - S3 Bucket: batch files and results
    - Step Function: orchestration with parallel processing

    Workflow:
    1. Initialize - Set up execution context
    2. PrepareReceipts - Query receipts with unlabeled words
    3. ProcessInBatches - Run suggestion agent (5 parallel by default)
    4. AggregateResults - Generate summary report

    Rate Limiting:
    - Default concurrency: 5 (configurable via Pulumi config)
    - OllamaRateLimitError retry: 5 retries, 30s backoff, 1.5x rate
    """

    def __init__(
        self,
        name: str,
        *,
        dynamodb_table_name: pulumi.Input[str],
        dynamodb_table_arn: pulumi.Input[str],
        chromadb_bucket_name: pulumi.Input[str],
        chromadb_bucket_arn: pulumi.Input[str],
        max_concurrency_process: Optional[int] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(f"{__name__}-{name}", name, None, opts)
        stack = pulumi.get_stack()

        # Use provided values or fall back to config or defaults
        self.max_concurrency_process = (
            max_concurrency_process or max_concurrency_process_default
        )

        # ============================================================
        # S3 Bucket for batch files and results
        # ============================================================
        self.batch_bucket = Bucket(
            f"{name}-batch-bucket",
            force_destroy=True,
            tags={"environment": stack, "purpose": "label-suggestion-batches"},
            opts=ResourceOptions(parent=self),
        )

        BucketVersioning(
            f"{name}-batch-bucket-versioning",
            bucket=self.batch_bucket.id,
            versioning_configuration=BucketVersioningVersioningConfigurationArgs(
                status="Enabled"
            ),
            opts=ResourceOptions(parent=self.batch_bucket),
        )

        # ============================================================
        # IAM Roles
        # ============================================================

        # Step Function role
        sfn_role = Role(
            f"{name}-sfn-role",
            name=f"{name}-sfn-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "states.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Lambda execution role
        lambda_role = Role(
            f"{name}-lambda-role",
            name=f"{name}-lambda-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Basic Lambda execution
        RolePolicyAttachment(
            f"{name}-lambda-basic-exec",
            role=lambda_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/"
                "AWSLambdaBasicExecutionRole"
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # ECR permissions for container Lambda
        RolePolicy(
            f"{name}-lambda-ecr-policy",
            role=lambda_role.id,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "ecr:GetAuthorizationToken",
                                "ecr:BatchGetImage",
                                "ecr:GetDownloadUrlForLayer",
                            ],
                            "Resource": "*",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # DynamoDB access policy
        RolePolicy(
            f"{name}-lambda-dynamo-policy",
            role=lambda_role.id,
            policy=Output.all(dynamodb_table_arn).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:DescribeTable",
                                    "dynamodb:GetItem",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:Query",
                                    "dynamodb:Scan",
                                    "dynamodb:BatchGetItem",
                                    "dynamodb:BatchWriteItem",
                                ],
                                "Resource": [
                                    args[0],
                                    f"{args[0]}/index/*",
                                ],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # S3 access policy (batch bucket + ChromaDB bucket)
        RolePolicy(
            f"{name}-lambda-s3-policy",
            role=lambda_role.id,
            policy=Output.all(
                self.batch_bucket.arn, chromadb_bucket_arn
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:PutObject",
                                    "s3:DeleteObject",
                                    "s3:ListBucket",
                                ],
                                "Resource": [
                                    args[0],
                                    f"{args[0]}/*",
                                    args[1],
                                    f"{args[1]}/*",
                                ],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # ============================================================
        # Zip Lambda: prepare_receipts
        # ============================================================
        CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
        HANDLERS_DIR = os.path.join(CURRENT_DIR, "handlers")

        prepare_receipts_lambda = Function(
            f"{name}-prepare-receipts",
            name=f"{name}-prepare-receipts",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="prepare_receipts.handler",
            code=AssetArchive(
                {
                    "prepare_receipts.py": FileAsset(
                        os.path.join(HANDLERS_DIR, "prepare_receipts.py")
                    ),
                }
            ),
            timeout=300,  # 5 minutes
            memory_size=512,
            layers=[dynamo_layer.arn] if dynamo_layer else [],
            tags={"environment": stack},
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                    "BATCH_BUCKET": self.batch_bucket.bucket,
                }
            ),
            opts=ResourceOptions(
                parent=self,
                ignore_changes=["layers"],
            ),
        )

        # Zip Lambda: load_batches
        load_batches_lambda = Function(
            f"{name}-load-batches",
            name=f"{name}-load-batches",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="load_batches.handler",
            code=AssetArchive(
                {
                    "load_batches.py": FileAsset(
                        os.path.join(HANDLERS_DIR, "load_batches.py")
                    ),
                }
            ),
            timeout=60,  # 1 minute
            memory_size=256,
            tags={"environment": stack},
            environment=FunctionEnvironmentArgs(
                variables={
                    "BATCH_BUCKET": self.batch_bucket.bucket,
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Zip Lambda: aggregate_results
        aggregate_results_lambda = Function(
            f"{name}-aggregate-results",
            name=f"{name}-aggregate-results",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="aggregate_results.handler",
            code=AssetArchive(
                {
                    "aggregate_results.py": FileAsset(
                        os.path.join(HANDLERS_DIR, "aggregate_results.py")
                    ),
                }
            ),
            timeout=120,  # 2 minutes
            memory_size=256,
            tags={"environment": stack},
            environment=FunctionEnvironmentArgs(
                variables={
                    "BATCH_BUCKET": self.batch_bucket.bucket,
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Container Lambda: suggest_labels
        # ============================================================
        suggest_lambda_config = {
            "role_arn": lambda_role.arn,
            "timeout": 900,  # 15 minutes
            "memory_size": 3072,  # 3 GB for ChromaDB + LLM
            "tags": {"environment": stack},
            "ephemeral_storage": 10240,  # 10 GB /tmp for ChromaDB snapshot
            "environment": {
                # Lambda-specific (used by handler directly)
                "BATCH_BUCKET": self.batch_bucket.bucket,
                "CHROMADB_BUCKET": chromadb_bucket_name,
                # receipt_agent Settings (RECEIPT_AGENT_* prefix)
                "RECEIPT_AGENT_DYNAMO_TABLE_NAME": dynamodb_table_name,
                "RECEIPT_AGENT_OPENAI_API_KEY": openai_api_key,
                "RECEIPT_AGENT_OLLAMA_API_KEY": ollama_api_key,
                "RECEIPT_AGENT_OLLAMA_BASE_URL": "https://ollama.com",
                "RECEIPT_AGENT_OLLAMA_MODEL": "gpt-oss:120b-cloud",
                # ChromaDB will be downloaded to /tmp, path set at runtime
                "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY": "/tmp/chromadb_words",
                # LangSmith tracing (enabled for debugging)
                "LANGCHAIN_API_KEY": langchain_api_key,
                "LANGCHAIN_TRACING_V2": "true",
                "LANGCHAIN_ENDPOINT": "https://api.smith.langchain.com",
                "LANGCHAIN_PROJECT": pulumi.Config("portfolio").get(
                    "langchain_project"
                )
                or "label-suggestion-agent",
            },
        }

        suggest_docker_image = CodeBuildDockerImage(
            f"{name}-suggest-img",
            dockerfile_path="infra/label_suggestion_step_functions/lambdas/Dockerfile",
            build_context_path=".",
            source_paths=[
                "receipt_agent",  # Include receipt_agent package
                "receipt_places",  # receipt_agent depends on receipt_places
            ],
            lambda_function_name=f"{name}-suggest-labels",
            lambda_config=suggest_lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_role]),
        )

        suggest_labels_lambda = suggest_docker_image.lambda_function

        # ============================================================
        # Step Function role policy
        # ============================================================
        RolePolicy(
            f"{name}-sfn-lambda-policy",
            role=sfn_role.id,
            policy=Output.all(
                prepare_receipts_lambda.arn,
                load_batches_lambda.arn,
                suggest_labels_lambda.arn,
                aggregate_results_lambda.arn,
            ).apply(
                lambda arns: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["lambda:InvokeFunction"],
                                "Resource": arns,
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=sfn_role),
        )

        # CloudWatch Logs policy for Step Function
        RolePolicy(
            f"{name}-sfn-logs-policy",
            role=sfn_role.id,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "logs:CreateLogDelivery",
                                "logs:GetLogDelivery",
                                "logs:UpdateLogDelivery",
                                "logs:DeleteLogDelivery",
                                "logs:ListLogDeliveries",
                                "logs:PutResourcePolicy",
                                "logs:DescribeResourcePolicies",
                                "logs:DescribeLogGroups",
                            ],
                            "Resource": "*",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=sfn_role),
        )

        # ============================================================
        # CloudWatch Log Group
        # ============================================================
        log_group = LogGroup(
            f"{name}-sf-logs",
            name=f"/aws/stepfunctions/{name}-sf",
            retention_in_days=14,
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Step Function State Machine
        # ============================================================
        logging_config = log_group.arn.apply(
            lambda arn: StateMachineLoggingConfigurationArgs(
                level="ALL",
                include_execution_data=True,
                log_destination=f"{arn}:*",
            )
        )

        self.state_machine = StateMachine(
            f"{name}-sf",
            name=f"{name}-sf",
            role_arn=sfn_role.arn,
            type="STANDARD",
            tags={"environment": stack},
            definition=Output.all(
                prepare_receipts_lambda.arn,
                load_batches_lambda.arn,
                suggest_labels_lambda.arn,
                aggregate_results_lambda.arn,
                self.batch_bucket.bucket,
            ).apply(
                lambda args: self._create_step_function_definition(
                    prepare_arn=args[0],
                    load_batches_arn=args[1],
                    suggest_arn=args[2],
                    aggregate_arn=args[3],
                    batch_bucket=args[4],
                    max_concurrency_process=self.max_concurrency_process,
                )
            ),
            logging_configuration=logging_config,
            opts=ResourceOptions(parent=self, depends_on=[log_group]),
        )

        # ============================================================
        # Outputs
        # ============================================================
        self.state_machine_arn = self.state_machine.arn
        self.batch_bucket_name = self.batch_bucket.bucket
        self.prepare_receipts_lambda_arn = prepare_receipts_lambda.arn
        self.suggest_labels_lambda_arn = suggest_labels_lambda.arn
        self.aggregate_results_lambda_arn = aggregate_results_lambda.arn

        self.register_outputs(
            {
                "state_machine_arn": self.state_machine.arn,
                "batch_bucket_name": self.batch_bucket.bucket,
                "prepare_receipts_lambda_arn": prepare_receipts_lambda.arn,
                "suggest_labels_lambda_arn": suggest_labels_lambda.arn,
                "aggregate_results_lambda_arn": aggregate_results_lambda.arn,
            }
        )

    def _create_step_function_definition(
        self,
        prepare_arn: str,
        load_batches_arn: str,
        suggest_arn: str,
        aggregate_arn: str,
        batch_bucket: str,
        max_concurrency_process: int,
    ) -> str:
        """Create Step Function definition (ASL)."""
        definition = {
            "Comment": "Label Suggestion Agent - Process receipts with unlabeled words",
            "StartAt": "Initialize",
            "States": {
                # Initialize execution context
                "Initialize": {
                    "Type": "Pass",
                    "Parameters": {
                        "execution_id.$": "$$.Execution.Name",
                        "start_time.$": "$$.Execution.StartTime",
                        "dry_run.$": "$.dry_run",
                        "max_receipts.$": "$.max_receipts",
                        "batch_bucket": batch_bucket,
                        "langchain_project.$": "$.langchain_project",
                    },
                    "ResultPath": "$.init",
                    "Next": "PrepareReceipts",
                },
                # Prepare receipts with unlabeled words
                "PrepareReceipts": {
                    "Type": "Task",
                    "Resource": prepare_arn,
                    "TimeoutSeconds": 300,
                    "Parameters": {
                        "execution_id.$": "$.init.execution_id",
                        "batch_bucket": batch_bucket,
                        "max_receipts.$": "$.init.max_receipts",
                    },
                    "ResultPath": "$.prepare_result",
                    "Retry": [
                        {
                            "ErrorEquals": [
                                "States.TaskFailed",
                                "Lambda.ServiceException",
                            ],
                            "IntervalSeconds": 2,
                            "MaxAttempts": 3,
                            "BackoffRate": 2.0,
                        }
                    ],
                    "Next": "LoadBatches",
                },
                "NoReceiptsToProcess": {
                    "Type": "Pass",
                    "Result": {
                        "message": "No receipts with unlabeled words to process"
                    },
                    "End": True,
                },
                # Load batch manifest from S3 and return batch indices
                "LoadBatches": {
                    "Type": "Task",
                    "Resource": load_batches_arn,
                    "TimeoutSeconds": 60,
                    "Parameters": {
                        "manifest_s3_key.$": "$.prepare_result.manifest_s3_key",
                        "execution_id.$": "$.init.execution_id",
                        "batch_bucket": batch_bucket,
                        "langchain_project.$": "$.init.langchain_project",
                    },
                    "ResultPath": "$.batches_data",
                    "Retry": [
                        {
                            "ErrorEquals": [
                                "States.TaskFailed",
                                "Lambda.ServiceException",
                            ],
                            "IntervalSeconds": 2,
                            "MaxAttempts": 2,
                            "BackoffRate": 2.0,
                        }
                    ],
                    "Next": "HasBatches",
                },
                # Check if there are batches to process
                "HasBatches": {
                    "Type": "Choice",
                    "Choices": [
                        {
                            "Variable": "$.batches_data.batch_indices[0]",
                            "IsPresent": True,
                            "Next": "ProcessInBatches",
                        }
                    ],
                    "Default": "NoReceiptsToProcess",
                },
                # Process batches in parallel
                # Use index-based pattern to avoid payload size limits
                "ProcessInBatches": {
                    "Type": "Map",
                    "ItemsPath": "$.batches_data.batch_indices",
                    "MaxConcurrency": max_concurrency_process,
                    "Parameters": {
                        "batch_index.$": "$$.Map.Item.Value",
                        "manifest_s3_key.$": "$.batches_data.manifest_s3_key",
                        "execution_id.$": "$.init.execution_id",
                        "batch_bucket": batch_bucket,
                        "dry_run.$": "$.init.dry_run",
                        "langchain_project.$": "$.batches_data.langchain_project",
                    },
                    "ItemProcessor": {
                        "ProcessorConfig": {"Mode": "INLINE"},
                        "StartAt": "SuggestLabels",
                        "States": {
                            "SuggestLabels": {
                                "Type": "Task",
                                "Resource": suggest_arn,
                                "TimeoutSeconds": 900,
                                "Parameters": {
                                    "batch_index.$": "$.batch_index",
                                    "manifest_s3_key.$": "$.manifest_s3_key",
                                    "execution_id.$": "$.execution_id",
                                    "batch_bucket": batch_bucket,
                                    "dry_run.$": "$.dry_run",
                                    "langchain_project.$": "$.langchain_project",
                                },
                                "Retry": [
                                    {
                                        "ErrorEquals": [
                                            "States.TaskFailed",
                                            "Lambda.ServiceException",
                                        ],
                                        "IntervalSeconds": 5,
                                        "MaxAttempts": 2,
                                        "BackoffRate": 2.0,
                                    },
                                    {
                                        "ErrorEquals": [
                                            "OllamaRateLimitError"
                                        ],
                                        "IntervalSeconds": 30,
                                        "MaxAttempts": 5,
                                        "BackoffRate": 1.5,
                                    },
                                ],
                                "ResultSelector": {
                                    "status.$": "$.status",
                                    "results_path.$": "$.results_path",
                                    "receipts_processed.$": "$.receipts_processed",
                                    "suggestions_made.$": "$.suggestions_made",
                                    "llm_calls.$": "$.llm_calls",
                                    "skipped_no_candidates.$": "$.skipped_no_candidates",
                                    "skipped_low_confidence.$": "$.skipped_low_confidence",
                                },
                                "End": True,
                            },
                        },
                    },
                    "ResultSelector": {
                        "batch_count.$": "States.ArrayLength($)"
                    },
                    "ResultPath": "$.process_results",
                    "Catch": [
                        {
                            "ErrorEquals": ["States.ALL"],
                            "Next": "ProcessFailed",
                            "ResultPath": "$.error",
                        }
                    ],
                    "Next": "AggregateResults",
                },
                # Aggregate all results
                # Read results from S3 to avoid 256KB payload limit
                "AggregateResults": {
                    "Type": "Task",
                    "Resource": aggregate_arn,
                    "TimeoutSeconds": 120,
                    "Parameters": {
                        "execution_id.$": "$.init.execution_id",
                        "batch_bucket": batch_bucket,
                        "dry_run.$": "$.init.dry_run",
                        "process_results.$": "$.process_results",
                    },
                    "ResultPath": "$.summary",
                    "Next": "Done",
                },
                # Success state
                "Done": {
                    "Type": "Pass",
                    "End": True,
                },
                # Error states
                "ProcessFailed": {
                    "Type": "Fail",
                    "Error": "ProcessReceiptsError",
                    "Cause": "Failed to process receipts",
                },
            },
        }

        return json.dumps(definition)
