"""
Pulumi infrastructure for Label Evaluator Step Function.

This component creates a Step Function that validates receipt word labels
using spatial pattern analysis across receipts from the same merchant.

Architecture:
- Zip Lambdas: list_receipts, fetch_receipt_data, compute_patterns, aggregate_results
- Container Lambda: evaluate_labels (compute-only graph)
- Container Lambda: llm_review (optional LLM-based review)
- S3 Bucket: batch files and results storage
- Step Function: orchestration with distributed map

Workflow:
1. ListReceipts - Query receipts by merchant
2. ComputePatterns - Compute merchant patterns ONCE (10GB, memory-intensive)
3. ProcessBatches - Distributed Map:
   a. FetchReceiptData - Get target receipt
   b. EvaluateLabels - Run compute-only graph with pre-computed patterns
   c. (Optional) LLMReview - Review with Ollama
4. AggregateResults - Generate summary report

Key optimization: Pattern computation is done ONCE before the distributed map,
then each receipt evaluation simply loads the pre-computed patterns from S3.
This reduces memory from 10GB per receipt to 1GB per receipt.
"""

import json
import os
from typing import Optional

import pulumi
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
from pulumi_aws.lambda_ import (
    Function,
    FunctionEnvironmentArgs,
)
from pulumi_aws.s3 import (
    Bucket,
    BucketVersioningV2,
    BucketVersioningV2VersioningConfigurationArgs,
)
from pulumi_aws.sfn import StateMachine, StateMachineLoggingConfigurationArgs

# Import shared components
try:
    from codebuild_docker_image import CodeBuildDockerImage
    from lambda_layer import dynamo_layer
except ImportError as e:
    raise ImportError(
        "Required modules 'codebuild_docker_image' and 'lambda_layer' not found. "
        "Ensure they are available in the Pulumi project."
    ) from e

# Load secrets from Pulumi config
config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")
ollama_api_key = config.require_secret("OLLAMA_API_KEY")
langchain_api_key = config.require_secret("LANGCHAIN_API_KEY")

# Label evaluator specific config
evaluator_config = Config("label-evaluator")
max_concurrency_default = evaluator_config.get_int("max_concurrency") or 10
batch_size_default = evaluator_config.get_int("batch_size") or 10


class LabelEvaluatorStepFunction(ComponentResource):
    """
    Step Function infrastructure for label evaluation.

    Validates receipt word labels by analyzing spatial patterns within
    receipts and across receipts from the same merchant.

    Components:
    - Zip Lambdas: Fast orchestration handlers
    - Container Lambda: evaluate_labels (compute-only graph)
    - Container Lambda: llm_review (optional)
    - S3 Bucket: Intermediate data and results
    - Step Function: Workflow orchestration
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
        super().__init__(f"{__name__}-{name}", name, None, opts)
        stack = pulumi.get_stack()

        self.max_concurrency = max_concurrency or max_concurrency_default
        self.batch_size = batch_size or batch_size_default

        # ============================================================
        # S3 Bucket for batch files and results
        # ============================================================
        self.batch_bucket = Bucket(
            f"{name}-batch-bucket",
            force_destroy=True,
            tags={"environment": stack, "purpose": "label-evaluator-batches"},
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
                                    "dynamodb:Query",
                                    "dynamodb:Scan",
                                    "dynamodb:BatchGetItem",
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

        # S3 access policy
        if chromadb_bucket_arn:
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
        else:
            RolePolicy(
                f"{name}-lambda-s3-policy",
                role=lambda_role.id,
                policy=self.batch_bucket.arn.apply(
                    lambda arn: json.dumps(
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
                                    "Resource": [arn, f"{arn}/*"],
                                }
                            ],
                        }
                    )
                ),
                opts=ResourceOptions(parent=lambda_role),
            )

        # ============================================================
        # Zip Lambdas
        # ============================================================
        CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
        HANDLERS_DIR = os.path.join(CURRENT_DIR, "handlers")
        UTILS_DIR = os.path.join(CURRENT_DIR, "lambdas", "utils")

        # list_merchants Lambda (new - lists unique merchants)
        list_merchants_lambda = Function(
            f"{name}-list-merchants",
            name=f"{name}-list-merchants",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="list_merchants.handler",
            code=AssetArchive(
                {
                    "list_merchants.py": FileAsset(
                        os.path.join(HANDLERS_DIR, "list_merchants.py")
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

        # list_receipts Lambda
        list_receipts_lambda = Function(
            f"{name}-list-receipts",
            name=f"{name}-list-receipts",
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

        # fetch_receipt_data Lambda
        fetch_receipt_data_lambda = Function(
            f"{name}-fetch-receipt-data",
            name=f"{name}-fetch-receipt-data",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="fetch_receipt_data.handler",
            code=AssetArchive(
                {
                    "fetch_receipt_data.py": FileAsset(
                        os.path.join(HANDLERS_DIR, "fetch_receipt_data.py")
                    ),
                    "serialization.py": FileAsset(
                        os.path.join(UTILS_DIR, "serialization.py")
                    ),
                }
            ),
            timeout=60,
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
        # Container Lambda: compute_patterns (memory-intensive)
        # Runs ONCE per merchant before distributed map
        # ============================================================
        compute_patterns_config = {
            "role_arn": lambda_role.arn,
            "timeout": 600,  # 10 minutes - pattern computation can be slow
            "memory_size": 10240,  # 10 GB max - pattern computation is memory intensive
            "tags": {"environment": stack},
            "ephemeral_storage": 512,
            "environment": {
                "BATCH_BUCKET": self.batch_bucket.bucket,
                "DYNAMODB_TABLE_NAME": dynamodb_table_name,
            },
        }

        compute_patterns_docker_image = CodeBuildDockerImage(
            f"{name}-compute-patterns-img",
            dockerfile_path=(
                "infra/label_evaluator_step_functions/lambdas/"
                "Dockerfile.compute_patterns"
            ),
            build_context_path=".",
            source_paths=[
                "receipt_dynamo",
                "receipt_dynamo_stream",
                "receipt_chroma",
                "receipt_places",
                "receipt_agent",
            ],
            lambda_function_name=f"{name}-compute-patterns",
            lambda_config=compute_patterns_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_role]),
        )

        compute_patterns_lambda = compute_patterns_docker_image.lambda_function

        # aggregate_results Lambda (per-merchant aggregation)
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
            timeout=120,
            memory_size=512,
            tags={"environment": stack},
            environment=FunctionEnvironmentArgs(
                variables={
                    "BATCH_BUCKET": self.batch_bucket.bucket,
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # final_aggregate Lambda (cross-merchant grand totals)
        final_aggregate_lambda = Function(
            f"{name}-final-aggregate",
            name=f"{name}-final-aggregate",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="final_aggregate.handler",
            code=AssetArchive(
                {
                    "final_aggregate.py": FileAsset(
                        os.path.join(HANDLERS_DIR, "final_aggregate.py")
                    ),
                }
            ),
            timeout=120,
            memory_size=512,
            tags={"environment": stack},
            environment=FunctionEnvironmentArgs(
                variables={
                    "BATCH_BUCKET": self.batch_bucket.bucket,
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Container Lambda: evaluate_labels
        # ============================================================
        evaluate_lambda_config = {
            "role_arn": lambda_role.arn,
            "timeout": 300,  # 5 minutes
            "memory_size": 512,  # 512MB - sufficient after removing circular refs
            "tags": {"environment": stack},
            "ephemeral_storage": 512,
            "environment": {
                "BATCH_BUCKET": self.batch_bucket.bucket,
                "RECEIPT_AGENT_DYNAMO_TABLE_NAME": dynamodb_table_name,
                "LANGCHAIN_API_KEY": langchain_api_key,
                "LANGCHAIN_TRACING_V2": "true",
                "LANGCHAIN_ENDPOINT": "https://api.smith.langchain.com",
                "LANGCHAIN_PROJECT": config.get("langchain_project")
                or "label-evaluator",
            },
        }

        evaluate_docker_image = CodeBuildDockerImage(
            f"{name}-evaluate-img",
            dockerfile_path=(
                "infra/label_evaluator_step_functions/lambdas/Dockerfile"
            ),
            build_context_path=".",
            source_paths=[
                "receipt_dynamo",
                "receipt_dynamo_stream",
                "receipt_chroma",
                "receipt_places",
                "receipt_agent",
            ],
            lambda_function_name=f"{name}-evaluate-labels",
            lambda_config=evaluate_lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_role]),
        )

        evaluate_labels_lambda = evaluate_docker_image.lambda_function

        # ============================================================
        # Container Lambda: llm_review (optional)
        # ============================================================
        llm_review_lambda_config = {
            "role_arn": lambda_role.arn,
            "timeout": 900,  # 15 minutes
            "memory_size": 3072,  # 3 GB for ChromaDB + LLM
            "tags": {"environment": stack},
            "ephemeral_storage": 10240,  # 10 GB for ChromaDB
            "environment": {
                "BATCH_BUCKET": self.batch_bucket.bucket,
                "CHROMADB_BUCKET": chromadb_bucket_name or "",
                "RECEIPT_AGENT_DYNAMO_TABLE_NAME": dynamodb_table_name,
                "RECEIPT_AGENT_OPENAI_API_KEY": openai_api_key,
                "RECEIPT_AGENT_OLLAMA_API_KEY": ollama_api_key,
                "RECEIPT_AGENT_OLLAMA_BASE_URL": "https://ollama.com",
                "RECEIPT_AGENT_OLLAMA_MODEL": "gpt-oss:20b-cloud",
                "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY": "/tmp/chromadb",
                "LANGCHAIN_API_KEY": langchain_api_key,
                "LANGCHAIN_TRACING_V2": "true",
                "LANGCHAIN_ENDPOINT": "https://api.smith.langchain.com",
                "LANGCHAIN_PROJECT": config.get("langchain_project")
                or "label-evaluator-llm",
            },
        }

        llm_review_docker_image = CodeBuildDockerImage(
            f"{name}-llm-review-img",
            dockerfile_path=(
                "infra/label_evaluator_step_functions/lambdas/Dockerfile.llm"
            ),
            build_context_path=".",
            source_paths=[
                "receipt_dynamo",
                "receipt_dynamo_stream",
                "receipt_chroma",
                "receipt_places",
                "receipt_agent",
            ],
            lambda_function_name=f"{name}-llm-review",
            lambda_config=llm_review_lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_role]),
        )

        llm_review_lambda = llm_review_docker_image.lambda_function

        # ============================================================
        # Step Function role policies
        # ============================================================
        RolePolicy(
            f"{name}-sfn-lambda-policy",
            role=sfn_role.id,
            policy=Output.all(
                list_merchants_lambda.arn,
                list_receipts_lambda.arn,
                fetch_receipt_data_lambda.arn,
                compute_patterns_lambda.arn,
                evaluate_labels_lambda.arn,
                llm_review_lambda.arn,
                aggregate_results_lambda.arn,
                final_aggregate_lambda.arn,
            ).apply(
                lambda arns: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["lambda:InvokeFunction"],
                                "Resource": list(arns),
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=sfn_role),
        )

        # CloudWatch Logs policy
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
                list_merchants_lambda.arn,
                list_receipts_lambda.arn,
                fetch_receipt_data_lambda.arn,
                compute_patterns_lambda.arn,
                evaluate_labels_lambda.arn,
                llm_review_lambda.arn,
                aggregate_results_lambda.arn,
                final_aggregate_lambda.arn,
                self.batch_bucket.bucket,
            ).apply(
                lambda args: self._create_step_function_definition(
                    list_merchants_arn=args[0],
                    list_receipts_arn=args[1],
                    fetch_receipt_data_arn=args[2],
                    compute_patterns_arn=args[3],
                    evaluate_labels_arn=args[4],
                    llm_review_arn=args[5],
                    aggregate_results_arn=args[6],
                    final_aggregate_arn=args[7],
                    batch_bucket=args[8],
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

        self.register_outputs(
            {
                "state_machine_arn": self.state_machine.arn,
                "batch_bucket_name": self.batch_bucket.bucket,
                "list_merchants_lambda_arn": list_merchants_lambda.arn,
                "list_receipts_lambda_arn": list_receipts_lambda.arn,
                "evaluate_labels_lambda_arn": evaluate_labels_lambda.arn,
                "llm_review_lambda_arn": llm_review_lambda.arn,
                "aggregate_results_lambda_arn": aggregate_results_lambda.arn,
                "final_aggregate_lambda_arn": final_aggregate_lambda.arn,
            }
        )

    def _create_step_function_definition(
        self,
        list_merchants_arn: str,
        list_receipts_arn: str,
        fetch_receipt_data_arn: str,
        compute_patterns_arn: str,
        evaluate_labels_arn: str,
        llm_review_arn: str,
        aggregate_results_arn: str,
        final_aggregate_arn: str,
        batch_bucket: str,
        max_concurrency: int,
        batch_size: int,
    ) -> str:
        """Create Step Function definition (ASL).

        Supports two modes:
        1. All merchants mode: No merchant_name provided, processes all qualifying merchants
        2. Single merchant mode: merchant_name provided, processes only that merchant

        For all merchants mode:
        - ListMerchants returns list of merchants with >= min_receipts
        - ProcessMerchants loops over each merchant
        - Each merchant goes through full evaluation workflow
        """
        definition = {
            "Comment": "Label Evaluator - Validate receipt labels using spatial patterns",
            "StartAt": "CheckInputMode",
            "States": {
                # Check if merchant_name is in input (before Initialize)
                "CheckInputMode": {
                    "Type": "Choice",
                    "Choices": [
                        {
                            "Variable": "$.merchant_name",
                            "IsPresent": True,
                            "Next": "InitializeSingleMerchant",
                        }
                    ],
                    "Default": "InitializeAllMerchants",
                },
                # Initialize for single merchant mode with defaults
                "InitializeSingleMerchant": {
                    "Type": "Pass",
                    "Parameters": {
                        "execution_id.$": "$$.Execution.Name",
                        "start_time.$": "$$.Execution.StartTime",
                        "batch_bucket": batch_bucket,
                        "batch_size": batch_size,
                        "merchant_name.$": "$.merchant_name",
                        "skip_llm_review.$": "$.skip_llm_review",
                        "max_training_receipts": 50,
                        "min_receipts": 5,
                        "limit": 1000,
                        "original_input.$": "$",
                    },
                    "ResultPath": "$.init",
                    "Next": "SingleMerchantMode",
                },
                # Initialize for all merchants mode with defaults
                "InitializeAllMerchants": {
                    "Type": "Pass",
                    "Parameters": {
                        "execution_id.$": "$$.Execution.Name",
                        "start_time.$": "$$.Execution.StartTime",
                        "batch_bucket": batch_bucket,
                        "batch_size": batch_size,
                        "merchant_name": None,
                        "skip_llm_review.$": "$.skip_llm_review",
                        "max_training_receipts": 50,
                        "min_receipts": 5,
                        "limit": 1000,
                        "original_input.$": "$",
                    },
                    "ResultPath": "$.init",
                    "Next": "ListMerchants",
                },
                # Single merchant mode - process just one merchant
                "SingleMerchantMode": {
                    "Type": "Pass",
                    "Parameters": {
                        "merchants": [
                            {
                                "merchant_name.$": "$.init.merchant_name",
                                "receipt_count": 0,
                            }
                        ],
                        "total_merchants": 1,
                        "mode": "single",
                    },
                    "ResultPath": "$.merchants_data",
                    "Next": "ProcessMerchants",
                },
                # List all merchants with sufficient receipts
                "ListMerchants": {
                    "Type": "Task",
                    "Resource": list_merchants_arn,
                    "TimeoutSeconds": 300,
                    "Parameters": {
                        "execution_id.$": "$.init.execution_id",
                        "batch_bucket.$": "$.init.batch_bucket",
                        "min_receipts.$": "$.init.min_receipts",
                        "max_training_receipts.$": "$.init.max_training_receipts",
                        "skip_llm_review.$": "$.init.skip_llm_review",
                    },
                    "ResultPath": "$.merchants_data",
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
                    "Next": "HasMerchants",
                },
                # Check if there are merchants to process
                "HasMerchants": {
                    "Type": "Choice",
                    "Choices": [
                        {
                            "Variable": "$.merchants_data.total_merchants",
                            "NumericGreaterThan": 0,
                            "Next": "ProcessMerchants",
                        }
                    ],
                    "Default": "NoMerchantsToProcess",
                },
                "NoMerchantsToProcess": {
                    "Type": "Pass",
                    "Result": {
                        "message": "No merchants found with sufficient receipts",
                        "total_merchants": 0,
                    },
                    "End": True,
                },
                # Process each merchant (outer loop)
                "ProcessMerchants": {
                    "Type": "Map",
                    "ItemsPath": "$.merchants_data.merchants",
                    "MaxConcurrency": 3,  # Process 3 merchants at a time
                    "Parameters": {
                        "merchant.$": "$$.Map.Item.Value",
                        "merchant_index.$": "$$.Map.Item.Index",
                        "execution_id.$": "$.init.execution_id",
                        "batch_bucket.$": "$.init.batch_bucket",
                        "batch_size.$": "$.init.batch_size",
                        "skip_llm_review.$": "$.init.skip_llm_review",
                        "max_training_receipts.$": "$.init.max_training_receipts",
                        "limit.$": "$.init.limit",
                    },
                    "ItemProcessor": {
                        "ProcessorConfig": {"Mode": "INLINE"},
                        "StartAt": "ListReceipts",
                        "States": {
                            # List receipts for this merchant
                            "ListReceipts": {
                                "Type": "Task",
                                "Resource": list_receipts_arn,
                                "TimeoutSeconds": 300,
                                "Parameters": {
                                    "execution_id.$": "$.execution_id",
                                    "batch_bucket.$": "$.batch_bucket",
                                    "batch_size.$": "$.batch_size",
                                    "merchant_name.$": "$.merchant.merchant_name",
                                    "max_training_receipts.$": (
                                        "$.max_training_receipts"
                                    ),
                                    "limit.$": "$.limit",
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
                            # Check if there are receipts
                            "HasReceipts": {
                                "Type": "Choice",
                                "Choices": [
                                    {
                                        "Variable": "$.receipts_data.total_receipts",
                                        "NumericGreaterThan": 0,
                                        "Next": "ComputePatterns",
                                    }
                                ],
                                "Default": "NoReceiptsForMerchant",
                            },
                            "NoReceiptsForMerchant": {
                                "Type": "Pass",
                                "Parameters": {
                                    "merchant_name.$": "$.merchant.merchant_name",
                                    "status": "skipped",
                                    "reason": "No receipts found",
                                    "total_receipts": 0,
                                },
                                "End": True,
                            },
                            # Compute patterns for this merchant
                            "ComputePatterns": {
                                "Type": "Task",
                                "Resource": compute_patterns_arn,
                                "TimeoutSeconds": 600,
                                "Parameters": {
                                    "execution_id.$": "$.execution_id",
                                    "batch_bucket.$": "$.batch_bucket",
                                    "merchant_name.$": "$.merchant.merchant_name",
                                    "max_training_receipts.$": (
                                        "$.max_training_receipts"
                                    ),
                                },
                                "ResultPath": "$.patterns_result",
                                "Retry": [
                                    {
                                        "ErrorEquals": [
                                            "States.TaskFailed",
                                            "Lambda.ServiceException",
                                        ],
                                        "IntervalSeconds": 5,
                                        "MaxAttempts": 2,
                                        "BackoffRate": 2.0,
                                    }
                                ],
                                "Next": "ProcessBatches",
                            },
                            # Process receipt batches
                            "ProcessBatches": {
                                "Type": "Map",
                                "ItemsPath": "$.receipts_data.receipt_batches",
                                "MaxConcurrency": max_concurrency,
                                "Parameters": {
                                    "batch.$": "$$.Map.Item.Value",
                                    "batch_index.$": "$$.Map.Item.Index",
                                    "execution_id.$": "$.execution_id",
                                    "batch_bucket.$": "$.batch_bucket",
                                    "skip_llm_review.$": "$.skip_llm_review",
                                    "patterns_s3_key.$": (
                                        "$.patterns_result.patterns_s3_key"
                                    ),
                                },
                                "ItemProcessor": {
                                    "ProcessorConfig": {"Mode": "INLINE"},
                                    "StartAt": "ProcessReceipts",
                                    "States": {
                                        # Inner map for each receipt in batch
                                        "ProcessReceipts": {
                                            "Type": "Map",
                                            "ItemsPath": "$.batch",
                                            "MaxConcurrency": 5,
                                            "Parameters": {
                                                "receipt.$": "$$.Map.Item.Value",
                                                "execution_id.$": "$.execution_id",
                                                "batch_bucket.$": "$.batch_bucket",
                                                "skip_llm_review.$": (
                                                    "$.skip_llm_review"
                                                ),
                                                "patterns_s3_key.$": (
                                                    "$.patterns_s3_key"
                                                ),
                                            },
                                            "ItemProcessor": {
                                                "ProcessorConfig": {
                                                    "Mode": "INLINE"
                                                },
                                                "StartAt": "FetchReceiptData",
                                                "States": {
                                                    "FetchReceiptData": {
                                                        "Type": "Task",
                                                        "Resource": (
                                                            fetch_receipt_data_arn
                                                        ),
                                                        "TimeoutSeconds": 60,
                                                        "Parameters": {
                                                            "receipt.$": "$.receipt",
                                                            "execution_id.$": (
                                                                "$.execution_id"
                                                            ),
                                                            "batch_bucket.$": (
                                                                "$.batch_bucket"
                                                            ),
                                                        },
                                                        "ResultPath": "$.receipt_data",
                                                        "Retry": [
                                                            {
                                                                "ErrorEquals": [
                                                                    "States.TaskFailed"
                                                                ],
                                                                "IntervalSeconds": 1,
                                                                "MaxAttempts": 2,
                                                                "BackoffRate": 2.0,
                                                            }
                                                        ],
                                                        "Next": "EvaluateLabels",
                                                    },
                                                    "EvaluateLabels": {
                                                        "Type": "Task",
                                                        "Resource": (
                                                            evaluate_labels_arn
                                                        ),
                                                        "TimeoutSeconds": 300,
                                                        "Parameters": {
                                                            "data_s3_key.$": (
                                                                "$.receipt_data"
                                                                ".data_s3_key"
                                                            ),
                                                            "patterns_s3_key.$": (
                                                                "$.patterns_s3_key"
                                                            ),
                                                            "execution_id.$": (
                                                                "$.execution_id"
                                                            ),
                                                            "batch_bucket.$": (
                                                                "$.batch_bucket"
                                                            ),
                                                        },
                                                        "ResultPath": "$.eval_result",
                                                        "Retry": [
                                                            {
                                                                "ErrorEquals": [
                                                                    "States.TaskFailed"
                                                                ],
                                                                "IntervalSeconds": 2,
                                                                "MaxAttempts": 2,
                                                                "BackoffRate": 2.0,
                                                            }
                                                        ],
                                                        "Next": "CheckSkipLLM",
                                                    },
                                                    "CheckSkipLLM": {
                                                        "Type": "Choice",
                                                        "Choices": [
                                                            {
                                                                "Variable": (
                                                                    "$.skip_llm_review"
                                                                ),
                                                                "BooleanEquals": False,
                                                                "Next": "LLMReview",
                                                            }
                                                        ],
                                                        "Default": "ReturnResult",
                                                    },
                                                    "LLMReview": {
                                                        "Type": "Task",
                                                        "Resource": llm_review_arn,
                                                        "TimeoutSeconds": 900,
                                                        "Parameters": {
                                                            "results_s3_key.$": (
                                                                "$.eval_result"
                                                                ".results_s3_key"
                                                            ),
                                                            "execution_id.$": (
                                                                "$.execution_id"
                                                            ),
                                                            "batch_bucket.$": (
                                                                "$.batch_bucket"
                                                            ),
                                                        },
                                                        "ResultPath": "$.llm_result",
                                                        "Retry": [
                                                            {
                                                                "ErrorEquals": [
                                                                    "States.TaskFailed"
                                                                ],
                                                                "IntervalSeconds": 5,
                                                                "MaxAttempts": 2,
                                                                "BackoffRate": 2.0,
                                                            }
                                                        ],
                                                        "Next": "ReturnResult",
                                                    },
                                                    "ReturnResult": {
                                                        "Type": "Pass",
                                                        "Parameters": {
                                                            "status.$": (
                                                                "$.eval_result.status"
                                                            ),
                                                            "image_id.$": (
                                                                "$.eval_result"
                                                                ".image_id"
                                                            ),
                                                            "receipt_id.$": (
                                                                "$.eval_result"
                                                                ".receipt_id"
                                                            ),
                                                            "issues_found.$": (
                                                                "$.eval_result"
                                                                ".issues_found"
                                                            ),
                                                            "results_s3_key.$": (
                                                                "$.eval_result"
                                                                ".results_s3_key"
                                                            ),
                                                        },
                                                        "End": True,
                                                    },
                                                },
                                            },
                                            "End": True,
                                        },
                                    },
                                },
                                "ResultPath": "$.batch_results",
                                "Next": "AggregateMerchantResults",
                            },
                            # Aggregate results for this merchant
                            "AggregateMerchantResults": {
                                "Type": "Task",
                                "Resource": aggregate_results_arn,
                                "TimeoutSeconds": 120,
                                "Parameters": {
                                    "execution_id.$": "$.execution_id",
                                    "batch_bucket.$": "$.batch_bucket",
                                    "process_results.$": "$.batch_results",
                                    "merchant_name.$": "$.merchant.merchant_name",
                                },
                                "ResultPath": "$.merchant_summary",
                                "Next": "ReturnMerchantResult",
                            },
                            "ReturnMerchantResult": {
                                "Type": "Pass",
                                "Parameters": {
                                    "merchant_name.$": "$.merchant.merchant_name",
                                    "status": "completed",
                                    "total_receipts.$": (
                                        "$.receipts_data.total_receipts"
                                    ),
                                    "summary.$": "$.merchant_summary",
                                },
                                "End": True,
                            },
                        },
                    },
                    "ResultPath": "$.all_merchant_results",
                    "Next": "FinalSummary",
                },
                # Final aggregation across all merchants
                "FinalSummary": {
                    "Type": "Task",
                    "Resource": final_aggregate_arn,
                    "TimeoutSeconds": 120,
                    "Parameters": {
                        "execution_id.$": "$.init.execution_id",
                        "batch_bucket.$": "$.init.batch_bucket",
                        "all_merchant_results.$": "$.all_merchant_results",
                    },
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
                    "End": True,
                },
            },
        }

        return json.dumps(definition)
