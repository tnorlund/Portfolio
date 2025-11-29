"""
Pulumi infrastructure for Label Validation Agent Step Function.

This component creates a Step Function that:
1. Prepares NEEDS_REVIEW labels (optionally filtered by CORE_LABEL(s))
2. Processes labels in batches (container Lambda with ChromaDB + LLM)
3. Aggregates results and generates reports

Architecture:
- Zip Lambda: prepare_labels (DynamoDB layer only, fast startup)
- Container Lambda: validate_labels (ChromaDB + LLM, heavy processing)
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
from pulumi_aws.s3 import Bucket, BucketVersioningV2, BucketVersioningV2VersioningConfigurationArgs
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

# Label validation agent specific config
validation_config = Config("label-validation-agent")
max_concurrency_process_default = validation_config.get_int("max_concurrency_process") or 2  # Conservative default for Ollama rate limits

# CORE_LABELS from receipt_label/constants.py
CORE_LABELS = [
    "MERCHANT_NAME",
    "STORE_HOURS",
    "PHONE_NUMBER",
    "WEBSITE",
    "LOYALTY_ID",
    "ADDRESS_LINE",
    "DATE",
    "TIME",
    "PAYMENT_METHOD",
    "COUPON",
    "DISCOUNT",
    "PRODUCT_NAME",
    "QUANTITY",
    "UNIT_PRICE",
    "LINE_TOTAL",
    "SUBTOTAL",
    "TAX",
    "GRAND_TOTAL",
]


class LabelValidationAgentStepFunction(ComponentResource):
    """
    Step Function infrastructure for label validation.

    Components:
    - Zip Lambda: prepare_labels (DynamoDB layer only, fast)
    - Container Lambda: validate_labels (ChromaDB + LLM)
    - S3 Bucket: batch files and results
    - Step Function: orchestration with parallel processing

    Workflow:
    1. Initialize - Set up execution context
    2. PrepareLabels - Query NEEDS_REVIEW labels (optionally filtered by CORE_LABEL(s))
    3. ProcessInBatches - Run validation agent (2 parallel by default, LLM-limited)
    4. AggregateResults - Generate summary report

    Rate Limiting:
    - Default concurrency: 2 (configurable via Pulumi config)
    - OllamaRateLimitError retry: 5 retries, 30s backoff, 1.5x rate
    - 0.5s delay between labels within a batch
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
        self.max_concurrency_process = max_concurrency_process or max_concurrency_process_default

        # ============================================================
        # S3 Bucket for batch files and results
        # ============================================================
        self.batch_bucket = Bucket(
            f"{name}-batch-bucket",
            force_destroy=True,
            tags={"environment": stack, "purpose": "label-validation-agent-batches"},
            opts=ResourceOptions(parent=self),
        )

        BucketVersioningV2(
            f"{name}-batch-bucket-versioning",
            bucket=self.batch_bucket.id,
            versioning_configuration=BucketVersioningV2VersioningConfigurationArgs(
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
        # Zip Lambda: prepare_labels
        # ============================================================
        CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
        HANDLERS_DIR = os.path.join(CURRENT_DIR, "handlers")

        prepare_labels_lambda = Function(
            f"{name}-prepare-labels",
            name=f"{name}-prepare-labels",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="prepare_labels.handler",
            code=AssetArchive(
                {
                    "prepare_labels.py": FileAsset(
                        os.path.join(HANDLERS_DIR, "prepare_labels.py")
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
        # Container Lambda: validate_labels
        # ============================================================
        validate_lambda_config = {
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
                "LANGCHAIN_PROJECT": pulumi.Config("portfolio").get("langchain_project") or "label-validation-agent",
            },
        }

        validate_docker_image = CodeBuildDockerImage(
            f"{name}-validate-img",
            dockerfile_path="infra/label_validation_agent_step_functions/lambdas/Dockerfile",
            build_context_path=".",
            source_paths=[
                "receipt_agent",  # Include receipt_agent package
                "receipt_places",  # receipt_agent depends on receipt_places
            ],
            lambda_function_name=f"{name}-validate-labels",
            lambda_config=validate_lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_role]),
        )

        validate_labels_lambda = validate_docker_image.lambda_function

        # ============================================================
        # Step Function role policy
        # ============================================================
        RolePolicy(
            f"{name}-sfn-lambda-policy",
            role=sfn_role.id,
            policy=Output.all(
                prepare_labels_lambda.arn,
                load_batches_lambda.arn,
                validate_labels_lambda.arn,
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
                prepare_labels_lambda.arn,
                load_batches_lambda.arn,
                validate_labels_lambda.arn,
                aggregate_results_lambda.arn,
                self.batch_bucket.bucket,
            ).apply(
                lambda args: self._create_step_function_definition(
                    prepare_arn=args[0],
                    load_batches_arn=args[1],
                    validate_arn=args[2],
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
        self.prepare_labels_lambda_arn = prepare_labels_lambda.arn
        self.validate_labels_lambda_arn = validate_labels_lambda.arn
        self.aggregate_results_lambda_arn = aggregate_results_lambda.arn

        self.register_outputs(
            {
                "state_machine_arn": self.state_machine.arn,
                "batch_bucket_name": self.batch_bucket.bucket,
                "prepare_labels_lambda_arn": prepare_labels_lambda.arn,
                "validate_labels_lambda_arn": validate_labels_lambda.arn,
                "aggregate_results_lambda_arn": aggregate_results_lambda.arn,
            }
        )

    def _create_step_function_definition(
        self,
        prepare_arn: str,
        load_batches_arn: str,
        validate_arn: str,
        aggregate_arn: str,
        batch_bucket: str,
        max_concurrency_process: int,
    ) -> str:
        """Create Step Function definition (ASL)."""
        definition = {
            "Comment": "Label Validation Agent - Process NEEDS_REVIEW labels",
            "StartAt": "Initialize",
            "States": {
                # Initialize execution context
                "Initialize": {
                    "Type": "Pass",
                    "Parameters": {
                        "execution_id.$": "$$.Execution.Name",
                        "start_time.$": "$$.Execution.StartTime",
                        "dry_run.$": "$.dry_run",
                        "max_labels.$": "$.max_labels",
                        "batch_bucket": batch_bucket,
                        "label_types.$": "$.label_types",
                        "min_confidence.$": "$.min_confidence",
                        "langchain_project.$": "$.langchain_project",
                    },
                    "ResultPath": "$.init",
                    "Next": "PrepareLabels",
                },
                # Prepare NEEDS_REVIEW labels (optionally filtered by CORE_LABEL(s))
                "PrepareLabels": {
                    "Type": "Task",
                    "Resource": prepare_arn,
                    "TimeoutSeconds": 300,
                    "Parameters": {
                        "execution_id.$": "$.init.execution_id",
                        "batch_bucket": batch_bucket,
                        "label_types.$": "$.init.label_types",
                        "max_labels.$": "$.init.max_labels",
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
                "NoLabelsToProcess": {
                    "Type": "Pass",
                    "Result": {"message": "No NEEDS_REVIEW labels to process"},
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
                    "Default": "NoLabelsToProcess",
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
                        "min_confidence.$": "$.init.min_confidence",
                        "langchain_project.$": "$.batches_data.langchain_project",
                    },
                    "ItemProcessor": {
                        "ProcessorConfig": {"Mode": "INLINE"},
                        "StartAt": "ValidateLabels",
                        "States": {
                            "ValidateLabels": {
                                "Type": "Task",
                                "Resource": validate_arn,
                                "TimeoutSeconds": 900,
                                "Parameters": {
                                    "batch_index.$": "$.batch_index",
                                    "manifest_s3_key.$": "$.manifest_s3_key",
                                    "execution_id.$": "$.execution_id",
                                    "batch_bucket": batch_bucket,
                                    "dry_run.$": "$.dry_run",
                                    "min_confidence.$": "$.min_confidence",
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
                                        "ErrorEquals": ["OllamaRateLimitError"],
                                        "IntervalSeconds": 30,
                                        "MaxAttempts": 5,
                                        "BackoffRate": 1.5,
                                    },
                                ],
                                "ResultSelector": {
                                    "status.$": "$.status",
                                    "results_path.$": "$.results_path",
                                    "labels_processed.$": "$.labels_processed",
                                    "valid_count.$": "$.valid_count",
                                    "invalid_count.$": "$.invalid_count",
                                    "needs_review_count.$": "$.needs_review_count",
                                    "updated_count.$": "$.updated_count",
                                    "skipped_count.$": "$.skipped_count",
                                    "failed_count.$": "$.failed_count",
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
                    "Error": "ProcessLabelsError",
                    "Cause": "Failed to process labels",
                },
            },
        }

        return json.dumps(definition)

