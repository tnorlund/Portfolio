"""
Pulumi infrastructure for Label Harmonizer Step Function.

This component creates a Step Function that:
1. Prepares labels in parallel (16 label types, zip Lambda)
2. Processes merchant groups in batches (container Lambda with ChromaDB + LLM)
3. Aggregates results and generates reports

Architecture:
- Zip Lambda: prepare_labels (DynamoDB layer only, fast startup)
- Container Lambda: harmonize_labels (ChromaDB + LLM, heavy processing)
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

# Label harmonizer specific config
harmonizer_config = Config("label-harmonizer")
max_concurrency_process_default = (
    harmonizer_config.get_int("max_concurrency_process") or 10
)
max_concurrency_prepare_default = (
    harmonizer_config.get_int("max_concurrency_prepare") or 18
)

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


class LabelHarmonizerStepFunction(ComponentResource):
    """
    Step Function infrastructure for label harmonization.

    Components:
    - Zip Lambda: prepare_labels (DynamoDB layer only, fast)
    - Container Lambda: harmonize_labels (ChromaDB + LLM)
    - S3 Bucket: batch files and results
    - Step Function: orchestration with parallel processing

    Workflow:
    1. Initialize - Set up execution context
    2. ParallelPrepare - Query DynamoDB for all label types (18 parallel)
    3. FlattenMerchantGroups - Combine work items
    4. ProcessInBatches - Run harmonization (10 parallel, LLM-limited)
    5. AggregateResults - Generate summary report
    """

    def __init__(
        self,
        name: str,
        *,
        dynamodb_table_name: pulumi.Input[str],
        dynamodb_table_arn: pulumi.Input[str],
        chromadb_bucket_name: pulumi.Input[str],
        chromadb_bucket_arn: pulumi.Input[str],
        max_concurrency_prepare: Optional[int] = None,
        max_concurrency_process: Optional[int] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(f"{__name__}-{name}", name, None, opts)
        stack = pulumi.get_stack()

        # Use provided values or fall back to config or defaults
        self.max_concurrency_prepare = (
            max_concurrency_prepare or max_concurrency_prepare_default
        )
        self.max_concurrency_process = (
            max_concurrency_process or max_concurrency_process_default
        )

        # ============================================================
        # S3 Bucket for batch files and results
        # ============================================================
        self.batch_bucket = Bucket(
            f"{name}-batch-bucket",
            force_destroy=True,
            tags={"environment": stack, "purpose": "label-harmonizer-batches"},
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

        # Zip Lambda: flatten_merchant_groups
        flatten_merchant_groups_lambda = Function(
            f"{name}-flatten-merchant-groups",
            name=f"{name}-flatten-merchant-groups",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="flatten_merchant_groups.handler",
            code=AssetArchive(
                {
                    "flatten_merchant_groups.py": FileAsset(
                        os.path.join(
                            HANDLERS_DIR, "flatten_merchant_groups.py"
                        )
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

        # Zip Lambda: load_work_items
        load_work_items_lambda = Function(
            f"{name}-load-work-items",
            name=f"{name}-load-work-items",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="load_work_items.handler",
            code=AssetArchive(
                {
                    "load_work_items.py": FileAsset(
                        os.path.join(HANDLERS_DIR, "load_work_items.py")
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
        # Container Lambda: harmonize_labels
        # ============================================================
        # Note: receipt_agent uses RECEIPT_AGENT_* prefixed env vars
        # via pydantic-settings. See receipt_agent/config/settings.py
        harmonize_lambda_config = {
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
                "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY": "/tmp/chromadb",
                # LangSmith tracing (enabled for debugging)
                "LANGCHAIN_API_KEY": langchain_api_key,
                "LANGCHAIN_TRACING_V2": "true",
                "LANGCHAIN_ENDPOINT": "https://api.smith.langchain.com",
                "LANGCHAIN_PROJECT": pulumi.Config("portfolio").get(
                    "langchain_project"
                )
                or "label-harmonizer",
            },
        }

        harmonize_docker_image = CodeBuildDockerImage(
            f"{name}-harmonize-img",
            dockerfile_path="infra/label_harmonizer_step_functions/lambdas/Dockerfile",
            build_context_path=".",
            source_paths=[
                "receipt_agent",  # Include receipt_agent package (not in default rsync)
                "receipt_places",  # receipt_agent depends on receipt_places
            ],
            lambda_function_name=f"{name}-harmonize-labels",
            lambda_config=harmonize_lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_role]),
        )

        harmonize_labels_lambda = harmonize_docker_image.lambda_function

        # ============================================================
        # Step Function role policy
        # ============================================================
        RolePolicy(
            f"{name}-sfn-lambda-policy",
            role=sfn_role.id,
            policy=Output.all(
                prepare_labels_lambda.arn,
                flatten_merchant_groups_lambda.arn,
                load_work_items_lambda.arn,
                harmonize_labels_lambda.arn,
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
                flatten_merchant_groups_lambda.arn,
                load_work_items_lambda.arn,
                harmonize_labels_lambda.arn,
                aggregate_results_lambda.arn,
                self.batch_bucket.bucket,
            ).apply(
                lambda args: self._create_step_function_definition(
                    prepare_arn=args[0],
                    flatten_arn=args[1],
                    load_work_items_arn=args[2],
                    harmonize_arn=args[3],
                    aggregate_arn=args[4],
                    batch_bucket=args[5],
                    max_concurrency_prepare=self.max_concurrency_prepare,
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
        self.harmonize_labels_lambda_arn = harmonize_labels_lambda.arn
        self.aggregate_results_lambda_arn = aggregate_results_lambda.arn

        self.register_outputs(
            {
                "state_machine_arn": self.state_machine.arn,
                "batch_bucket_name": self.batch_bucket.bucket,
                "prepare_labels_lambda_arn": prepare_labels_lambda.arn,
                "harmonize_labels_lambda_arn": harmonize_labels_lambda.arn,
                "aggregate_results_lambda_arn": aggregate_results_lambda.arn,
            }
        )

    def _create_step_function_definition(
        self,
        prepare_arn: str,
        flatten_arn: str,
        load_work_items_arn: str,
        harmonize_arn: str,
        aggregate_arn: str,
        batch_bucket: str,
        max_concurrency_prepare: int,
        max_concurrency_process: int,
    ) -> str:
        """Create Step Function definition (ASL)."""
        definition = {
            "Comment": "Label Harmonizer - Parallel processing with batch control",
            "StartAt": "Initialize",
            "States": {
                # Initialize execution context
                # Note: Input should always include max_merchants and label_types (even as null)
                # to avoid JSONPath errors. The script handles this.
                "Initialize": {
                    "Type": "Pass",
                    "Parameters": {
                        "execution_id.$": "$$.Execution.Name",
                        "start_time.$": "$$.Execution.StartTime",
                        "dry_run.$": "$.dry_run",
                        "max_merchants.$": "$.max_merchants",
                        "batch_bucket": batch_bucket,
                        "label_types.$": "$.label_types",
                        "langchain_project.$": "$.langchain_project",
                    },
                    "ResultPath": "$.init",
                    "Next": "CheckLabelTypes",
                },
                # Set default label_types if not provided or null
                # Check if label_types is present AND not null (null values should use defaults)
                "CheckLabelTypes": {
                    "Type": "Choice",
                    "Choices": [
                        {
                            "And": [
                                {
                                    "Variable": "$.init.label_types",
                                    "IsPresent": True,
                                },
                                {
                                    "Variable": "$.init.label_types",
                                    "IsNull": False,
                                },
                            ],
                            "Next": "UseProvidedLabelTypes",
                        }
                    ],
                    "Default": "UseDefaultLabelTypes",
                },
                "UseDefaultLabelTypes": {
                    "Type": "Pass",
                    "Parameters": {
                        "execution_id.$": "$.init.execution_id",
                        "start_time.$": "$.init.start_time",
                        "dry_run.$": "$.init.dry_run",
                        "max_merchants.$": "$.init.max_merchants",
                        "batch_bucket": batch_bucket,
                        "label_types": CORE_LABELS,
                        "langchain_project.$": "$.init.langchain_project",
                    },
                    "Next": "ParallelPrepare",
                },
                "UseProvidedLabelTypes": {
                    "Type": "Pass",
                    "Parameters": {
                        "execution_id.$": "$.init.execution_id",
                        "start_time.$": "$.init.start_time",
                        "dry_run.$": "$.init.dry_run",
                        "max_merchants.$": "$.init.max_merchants",
                        "batch_bucket": batch_bucket,
                        "label_types.$": "$.init.label_types",
                        "langchain_project.$": "$.init.langchain_project",
                    },
                    "Next": "ParallelPrepare",
                },
                # Prepare all label types in parallel
                "ParallelPrepare": {
                    "Type": "Map",
                    "ItemsPath": "$.label_types",
                    "MaxConcurrency": max_concurrency_prepare,
                    "Parameters": {
                        "label_type.$": "$$.Map.Item.Value",
                        "execution_id.$": "$.execution_id",
                        "batch_bucket.$": "$.batch_bucket",
                        "max_merchants.$": "$.max_merchants",
                    },
                    "ItemProcessor": {
                        "ProcessorConfig": {"Mode": "INLINE"},
                        "StartAt": "PrepareLabels",
                        "States": {
                            "PrepareLabels": {
                                "Type": "Task",
                                "Resource": prepare_arn,
                                "TimeoutSeconds": 300,
                                "ResultSelector": {
                                    "manifest_s3_key.$": "$.manifest_s3_key"
                                },
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
                                "End": True,
                            }
                        },
                    },
                    "ResultPath": "$.prepare_results",
                    "Catch": [
                        {
                            "ErrorEquals": ["States.ALL"],
                            "Next": "PrepareFailed",
                            "ResultPath": "$.error",
                        }
                    ],
                    "Next": "FlattenMerchantGroups",
                },
                # Flatten all merchant groups - reads manifests from S3 and creates combined manifest
                # prepare_results is now an array of manifest_s3_key strings (one per label type)
                "FlattenMerchantGroups": {
                    "Type": "Task",
                    "Resource": flatten_arn,
                    "TimeoutSeconds": 120,
                    "Parameters": {
                        "execution_id.$": "$.execution_id",
                        "batch_bucket.$": "$.batch_bucket",
                        "prepare_results.$": "$.prepare_results",
                    },
                    "ResultPath": "$.flatten_result",
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
                    "Next": "LoadWorkItems",
                },
                # Load work items from S3 manifest
                "LoadWorkItems": {
                    "Type": "Task",
                    "Resource": load_work_items_arn,
                    "TimeoutSeconds": 60,
                    "Parameters": {
                        "work_items_manifest_s3_key.$": "$.flatten_result.work_items_manifest_s3_key",
                        "execution_id.$": "$.execution_id",
                        "batch_bucket.$": "$.batch_bucket",
                        "langchain_project.$": "$.langchain_project",
                    },
                    "ResultPath": "$.work_items_data",
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
                    "Next": "HasWorkItems",
                },
                # Check if there are work items
                "HasWorkItems": {
                    "Type": "Choice",
                    "Choices": [
                        {
                            "Variable": "$.work_items_data.work_item_indices[0]",
                            "IsPresent": True,
                            "Next": "ProcessInBatches",
                        }
                    ],
                    "Default": "NoLabelsToProcess",
                },
                "NoLabelsToProcess": {
                    "Type": "Pass",
                    "Result": {"message": "No labels to process"},
                    "End": True,
                },
                # Process merchant groups in batches
                # Use index-based pattern to avoid payload size limits
                "ProcessInBatches": {
                    "Type": "Map",
                    "ItemsPath": "$.work_items_data.work_item_indices",
                    "MaxConcurrency": max_concurrency_process,
                    "Parameters": {
                        "index.$": "$$.Map.Item.Value",
                        "work_items_manifest_s3_key.$": "$.work_items_data.work_items_manifest_s3_key",
                        "execution_id.$": "$.execution_id",
                        "batch_bucket.$": "$.batch_bucket",
                        "dry_run.$": "$.dry_run",
                        "langchain_project.$": "$.work_items_data.langchain_project",
                    },
                    "ItemProcessor": {
                        "ProcessorConfig": {"Mode": "INLINE"},
                        "StartAt": "HarmonizeLabels",
                        "States": {
                            "HarmonizeLabels": {
                                "Type": "Task",
                                "Resource": harmonize_arn,
                                "TimeoutSeconds": 900,
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
                                # Minimize output to stay under 256KB payload limit
                                # Full results are saved to S3 at results_path
                                "ResultSelector": {
                                    "status.$": "$.status",
                                    "results_path.$": "$.results_path",
                                    "label_type.$": "$.label_type",
                                    "merchant_name.$": "$.merchant_name",
                                    "labels_processed.$": "$.labels_processed",
                                    "outliers_found.$": "$.outliers_found",
                                    "updates_applied.$": "$.updates_applied",
                                    "total_updated.$": "$.total_updated",
                                    "total_skipped.$": "$.total_skipped",
                                    "total_failed.$": "$.total_failed",
                                    "total_needs_review.$": "$.total_needs_review",
                                },
                                "End": True,
                            }
                        },
                    },
                    # Store only count to avoid 256KB payload limit
                    # Full results are in S3 at results/{execution_id}/{label_type}/{merchant}.json
                    # AggregateResults will read from S3
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
                        "execution_id.$": "$.execution_id",
                        "batch_bucket.$": "$.batch_bucket",
                        "dry_run.$": "$.dry_run",
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
                "PrepareFailed": {
                    "Type": "Fail",
                    "Error": "PrepareLabelsError",
                    "Cause": "Failed to prepare labels",
                },
                "ProcessFailed": {
                    "Type": "Fail",
                    "Error": "ProcessLabelsError",
                    "Cause": "Failed to process labels",
                },
            },
        }

        return json.dumps(definition)


class LabelHarmonizerV3StepFunction(ComponentResource):
    """
    Step Function infrastructure for label harmonization V3 (whole receipt processing).

    Components:
    - Zip Lambda: list_receipts (DynamoDB layer only, fast)
    - Container Lambda: harmonize_labels_v3 (LLM agent for whole receipts)
    - S3 Bucket: batch files and results
    - Step Function: orchestration with Map state for parallel processing

    Workflow:
    1. ListReceipts - Query DynamoDB for all receipts and batch them
    2. ProcessInBatches - Run harmonization for each batch (parallel)
    3. Done - Aggregate results
    """

    def __init__(
        self,
        name: str,
        *,
        dynamodb_table_name: pulumi.Input[str],
        dynamodb_table_arn: pulumi.Input[str],
        chromadb_bucket_name: Optional[pulumi.Input[str]] = None,
        chromadb_bucket_arn: Optional[pulumi.Input[str]] = None,
        max_concurrency: Optional[int] = None,
        batch_size: Optional[int] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(f"{__name__}-v3-{name}", name, None, opts)
        stack = pulumi.get_stack()
        self._name = name

        # Use provided values or fall back to defaults
        self.max_concurrency = max_concurrency or 5
        self.batch_size = batch_size or 50

        # ============================================================
        # S3 Bucket for batch files and results
        # ============================================================
        self.batch_bucket = Bucket(
            f"{name}-v3-batch-bucket",
            force_destroy=True,
            tags={
                "environment": stack,
                "purpose": "label-harmonizer-v3-batches",
            },
            opts=ResourceOptions(parent=self),
        )

        BucketVersioning(
            f"{name}-v3-batch-bucket-versioning",
            bucket=self.batch_bucket.id,
            versioning_configuration=BucketVersioningVersioningConfigurationArgs(
                status="Enabled"
            ),
            opts=ResourceOptions(parent=self.batch_bucket),
        )

        # ============================================================
        # IAM Roles (similar to V2 but simpler)
        # ============================================================
        sfn_role = Role(
            f"{name}-v3-sfn-role",
            name=f"{name}-v3-sfn-role",
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

        lambda_role = Role(
            f"{name}-v3-lambda-role",
            name=f"{name}-v3-lambda-role",
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

        RolePolicyAttachment(
            f"{name}-v3-lambda-basic-exec",
            role=lambda_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=lambda_role),
        )

        # ECR permissions for container Lambda
        RolePolicy(
            f"{name}-v3-lambda-ecr-policy",
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
        dynamodb_policy = RolePolicy(
            f"{name}-v3-lambda-dynamo-policy",
            role=lambda_role.id,
            policy=Output.from_input(dynamodb_table_arn).apply(
                lambda arn: json.dumps(
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
                                "Resource": [arn, f"{arn}/index/*"],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # S3 access policy
        s3_resources = [self.batch_bucket.arn]
        if chromadb_bucket_arn:
            s3_resources.append(chromadb_bucket_arn)

        s3_policy = Output.all(*s3_resources).apply(
            lambda arns: json.dumps(
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
                            "Resource": [arns[0], f"{arns[0]}/*"],
                        }
                    ]
                    + (
                        [
                            {
                                "Effect": "Allow",
                                "Action": ["s3:GetObject", "s3:ListBucket"],
                                "Resource": [arns[1], f"{arns[1]}/*"],
                            }
                        ]
                        if len(arns) > 1
                        else []
                    ),
                }
            )
        )

        RolePolicy(
            f"{name}-v3-lambda-s3-policy",
            role=lambda_role.id,
            policy=s3_policy,
            opts=ResourceOptions(parent=lambda_role),
        )

        # ============================================================
        # Zip Lambda: list_receipts
        # ============================================================
        CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
        HANDLERS_DIR = os.path.join(CURRENT_DIR, "handlers")

        list_receipts_lambda = Function(
            f"{name}-v3-list-receipts",
            name=f"{name}-v3-list-receipts",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="list_receipts.handler",
            code=AssetArchive(
                {
                    "list_receipts.py": FileAsset(
                        os.path.join(HANDLERS_DIR, "list_receipts.py")
                    ),
                }
            ),
            timeout=300,
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

        # ============================================================
        # Container Lambda: harmonize_labels_v3
        # ============================================================
        harmonize_lambda_config = {
            "role_arn": lambda_role.arn,
            "timeout": 900,
            "memory_size": 3072,
            "tags": {"environment": stack},
            "ephemeral_storage": 10240,
            "environment": {
                "BATCH_BUCKET": self.batch_bucket.bucket,
                "CHROMADB_BUCKET": chromadb_bucket_name or "",
                "RECEIPT_AGENT_DYNAMO_TABLE_NAME": dynamodb_table_name,
                "RECEIPT_AGENT_OPENAI_API_KEY": openai_api_key,
                "RECEIPT_AGENT_OLLAMA_API_KEY": ollama_api_key,
                "RECEIPT_AGENT_OLLAMA_BASE_URL": "https://ollama.com",
                "RECEIPT_AGENT_OLLAMA_MODEL": "gpt-oss:120b-cloud",
                "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY": "/tmp/chromadb",
                "LANGCHAIN_API_KEY": langchain_api_key,
                "LANGCHAIN_TRACING_V2": "true",
                "LANGCHAIN_ENDPOINT": "https://api.smith.langchain.com",
                "LANGCHAIN_PROJECT": pulumi.Config("portfolio").get(
                    "langchain_project"
                )
                or "label-harmonizer-v3",
            },
        }

        harmonize_docker_image = CodeBuildDockerImage(
            f"{name}-v3-harmonize-img",
            dockerfile_path="infra/label_harmonizer_step_functions/lambdas/Dockerfile.v3",
            build_context_path=".",
            source_paths=[
                "receipt_agent",
                "receipt_places",
                "receipt_upload",
            ],
            lambda_function_name=f"{name}-v3-harmonize-labels",
            lambda_config=harmonize_lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(
                parent=self, depends_on=[lambda_role, dynamodb_policy]
            ),
        )

        harmonize_labels_v3_lambda = harmonize_docker_image.lambda_function

        # ============================================================
        # Step Function role policy
        # ============================================================
        RolePolicy(
            f"{name}-v3-sfn-lambda-policy",
            role=sfn_role.id,
            policy=Output.all(
                list_receipts_lambda.arn,
                harmonize_labels_v3_lambda.arn,
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

        # CloudWatch Logs policy
        RolePolicy(
            f"{name}-v3-sfn-logs-policy",
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
            f"{name}-v3-sf-logs",
            name=f"/aws/stepfunctions/{name}-v3-sf",
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
            f"{name}-v3-sf",
            name=f"{name}-v3-sf",
            role_arn=sfn_role.arn,
            type="STANDARD",
            tags={"environment": stack},
            definition=Output.all(
                list_receipts_lambda.arn,
                harmonize_labels_v3_lambda.arn,
                self.batch_bucket.bucket,
            ).apply(
                lambda args: self._create_step_function_definition(
                    list_receipts_arn=args[0],
                    harmonize_arn=args[1],
                    batch_bucket=args[2],
                    max_concurrency=self.max_concurrency,
                    batch_size=self.batch_size,
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
        self.list_receipts_lambda_arn = list_receipts_lambda.arn
        self.harmonize_labels_v3_lambda_arn = harmonize_labels_v3_lambda.arn

        self.register_outputs(
            {
                "state_machine_arn": self.state_machine.arn,
                "batch_bucket_name": self.batch_bucket.bucket,
                "list_receipts_lambda_arn": list_receipts_lambda.arn,
                "harmonize_labels_v3_lambda_arn": harmonize_labels_v3_lambda.arn,
            }
        )

    def _create_step_function_definition(
        self,
        list_receipts_arn: str,
        harmonize_arn: str,
        batch_bucket: str,
        max_concurrency: int,
        batch_size: int,
    ) -> str:
        """Create Step Function definition (ASL)."""
        definition = {
            "Comment": "Label Harmonizer V3 - Process receipts in parallel",
            "StartAt": "Initialize",
            "States": {
                "Initialize": {
                    "Type": "Pass",
                    "Parameters": {
                        "execution_id.$": "$$.Execution.Name",
                        "start_time.$": "$$.Execution.StartTime",
                        "dry_run.$": "$.dry_run",
                        "batch_bucket": batch_bucket,
                        "batch_size": batch_size,
                        "limit.$": "$.limit",
                        "langchain_project.$": "$.langchain_project",
                    },
                    "ResultPath": "$.init",
                    "Next": "ListReceipts",
                },
                "ListReceipts": {
                    "Type": "Task",
                    "Resource": list_receipts_arn,
                    "TimeoutSeconds": 300,
                    "Parameters": {
                        "execution_id.$": "$.init.execution_id",
                        "batch_bucket.$": "$.init.batch_bucket",
                        "batch_size.$": "$.init.batch_size",
                        "limit.$": "$.init.limit",
                    },
                    "ResultPath": "$.receipts_data",
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
                    "Next": "HasReceipts",
                },
                "HasReceipts": {
                    "Type": "Choice",
                    "Choices": [
                        {
                            "Variable": "$.receipts_data.receipt_batches[0]",
                            "IsPresent": True,
                            "Next": "ProcessInBatches",
                        }
                    ],
                    "Default": "NoReceiptsToProcess",
                },
                "NoReceiptsToProcess": {
                    "Type": "Pass",
                    "Result": {"message": "No receipts to process"},
                    "End": True,
                },
                "ProcessInBatches": {
                    "Type": "Map",
                    "ItemsPath": "$.receipts_data.receipt_batches",
                    "MaxConcurrency": max_concurrency,
                    "Parameters": {
                        "receipts.$": "$$.Map.Item.Value",
                        "execution_id.$": "$.init.execution_id",
                        "dry_run.$": "$.init.dry_run",
                        "langchain_project.$": "$.init.langchain_project",
                        "batch_bucket.$": "$.init.batch_bucket",
                    },
                    "ItemProcessor": {
                        "ProcessorConfig": {"Mode": "INLINE"},
                        "StartAt": "HarmonizeLabels",
                        "States": {
                            "HarmonizeLabels": {
                                "Type": "Task",
                                "Resource": harmonize_arn,
                                "TimeoutSeconds": 900,
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
                                "End": True,
                            }
                        },
                    },
                    "ResultPath": "$.process_results",
                    "Catch": [
                        {
                            "ErrorEquals": ["States.ALL"],
                            "Next": "ProcessFailed",
                            "ResultPath": "$.error",
                        }
                    ],
                    "Next": "Done",
                },
                "Done": {
                    "Type": "Pass",
                    "End": True,
                },
                "ProcessFailed": {
                    "Type": "Fail",
                    "Error": "ProcessLabelsError",
                    "Cause": "Failed to process label harmonization",
                },
            },
        }

        return json.dumps(definition)
