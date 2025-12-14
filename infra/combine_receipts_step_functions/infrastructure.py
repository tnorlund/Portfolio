"""
Pulumi infrastructure for Combine Receipts Step Function.

This component creates a Step Function that:
1. Lists images with multiple receipts (from LLM analysis or DynamoDB query)
2. Combines receipts in parallel (container Lambda with image processing)
3. Creates embeddings and ChromaDB deltas
4. Aggregates results

Architecture:
- Zip Lambda: list_images (DynamoDB layer only, fast)
- Container Lambda: combine_receipts (PIL, image processing, embeddings)
- S3 Bucket: batch files and results
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
    BucketVersioningV2,
    BucketVersioningV2VersioningConfigurationArgs,
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

# Combine receipts specific config
combine_config = Config("combine-receipts")
max_concurrency_default = combine_config.get_int("max_concurrency") or 5


class CombineReceiptsStepFunction(ComponentResource):
    """
    Step Function infrastructure for combining receipts.

    Components:
    - Zip Lambda: list_images (DynamoDB layer only, fast)
    - Container Lambda: combine_receipts (PIL, image processing, embeddings)
    - S3 Bucket: batch files and results
    - Step Function: orchestration with parallel execution

    Workflow:
    1. Initialize - Set up execution context
    2. ListImages - Query DynamoDB or load from LLM analysis
    3. CombineReceipts - Process images in parallel (5 concurrent)
    4. AggregateResults - Generate summary report
    """

    def __init__(
        self,
        name: str,
        *,
        dynamodb_table_name: pulumi.Input[str],
        dynamodb_table_arn: pulumi.Input[str],
        chromadb_bucket_name: pulumi.Input[str],
        chromadb_bucket_arn: pulumi.Input[str],
        raw_bucket_name: pulumi.Input[str],
        site_bucket_name: pulumi.Input[str],
        artifacts_bucket_name: Optional[pulumi.Input[str]] = None,
        artifacts_bucket_arn: Optional[pulumi.Input[str]] = None,
        embed_ndjson_queue_arn: Optional[pulumi.Input[str]] = None,
        max_concurrency: Optional[int] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(f"{__name__}-{name}", name, None, opts)
        stack = pulumi.get_stack()

        # Use provided values or fall back to config or defaults
        self.max_concurrency = max_concurrency or max_concurrency_default

        # ============================================================
        # S3 Bucket for batch files and results
        # ============================================================
        self.batch_bucket = Bucket(
            f"{name}-batch-bucket",
            force_destroy=True,
            tags={"environment": stack, "purpose": "combine-receipts-batches"},
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

        # DynamoDB permissions
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
                                    "dynamodb:DeleteItem",
                                    "dynamodb:Query",
                                    "dynamodb:Scan",
                                    "dynamodb:BatchGetItem",
                                    "dynamodb:BatchWriteItem",
                                ],
                                "Resource": [args[0], f"{args[0]}/index/*"],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # S3 permissions
        # Build S3 resource list using Output.all to combine all bucket ARNs
        s3_resource_inputs = [
            self.batch_bucket.arn,
            chromadb_bucket_arn,
            raw_bucket_name,
            site_bucket_name,
        ]

        # Add artifacts bucket if provided (required for NDJSON export)
        if artifacts_bucket_arn:
            s3_resource_inputs.append(artifacts_bucket_arn)

        RolePolicy(
            f"{name}-lambda-s3-policy",
            role=lambda_role.id,
            policy=Output.all(*s3_resource_inputs).apply(
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
                                ],
                                "Resource": [
                                    args[0],  # batch bucket ARN
                                    f"{args[0]}/*",  # batch bucket objects
                                    args[1],  # chromadb bucket ARN
                                    f"{args[1]}/*",  # chromadb bucket objects
                                    f"arn:aws:s3:::{args[2]}/*",  # raw bucket objects
                                    f"arn:aws:s3:::{args[3]}/*",  # site bucket objects
                                ]
                                + (
                                    [args[4], f"{args[4]}/*"]
                                    if len(args) > 4
                                    else []  # artifacts bucket if provided
                                ),
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # SQS permissions (only if queue ARN is provided - no wildcards)
        if embed_ndjson_queue_arn:
            RolePolicy(
                f"{name}-lambda-sqs-policy",
                role=lambda_role.id,
                policy=Output.all(embed_ndjson_queue_arn).apply(
                    lambda args: json.dumps(
                        {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Action": [
                                        "sqs:SendMessage",
                                        "sqs:GetQueueAttributes",
                                    ],
                                    "Resource": args[
                                        0
                                    ],  # Specific queue ARN, no wildcards
                                }
                            ],
                        }
                    )
                ),
                opts=ResourceOptions(parent=lambda_role),
            )

        # ============================================================
        # Lambda Functions
        # ============================================================

        # List images Lambda (zip)
        list_images_lambda = Function(
            f"{name}-list-images",
            name=f"{name}-list-images",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="handlers.list_images.handler",
            code=AssetArchive(
                {
                    "handlers/__init__.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "handlers",
                            "__init__.py",
                        )
                    ),
                    "handlers/list_images.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "handlers",
                            "list_images.py",
                        )
                    ),
                }
            ),
            timeout=300,
            memory_size=512,
            layers=[dynamo_layer.arn],
            tags={"environment": stack},
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                    "BATCH_BUCKET": self.batch_bucket.bucket,
                }
            ),
            opts=ResourceOptions(
                parent=self,
                depends_on=[lambda_role],
                ignore_changes=["layers"],
            ),
        )

        # Aggregate results Lambda (zip)
        aggregate_results_lambda = Function(
            f"{name}-aggregate-results",
            name=f"{name}-aggregate-results",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="handlers.aggregate_results.handler",
            code=AssetArchive(
                {
                    "handlers/__init__.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "handlers",
                            "__init__.py",
                        )
                    ),
                    "handlers/aggregate_results.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "handlers",
                            "aggregate_results.py",
                        )
                    ),
                }
            ),
            timeout=120,
            memory_size=256,
            layers=[dynamo_layer.arn],
            tags={"environment": stack},
            environment=FunctionEnvironmentArgs(
                variables={
                    "BATCH_BUCKET": self.batch_bucket.bucket,
                }
            ),
            opts=ResourceOptions(
                parent=self,
                depends_on=[lambda_role],
                ignore_changes=["layers"],
            ),
        )

        # Combine receipts Lambda (container - needs PIL and image processing)
        combine_lambda_config = {
            "role_arn": lambda_role.arn,
            "timeout": 900,  # 15 minutes
            "memory_size": 3072,  # 3 GB for image processing + embeddings
            "tags": {"environment": stack},
            "ephemeral_storage": 10240,  # 10 GB /tmp for images and ChromaDB
            "environment": {
                "BATCH_BUCKET": self.batch_bucket.bucket,
                "CHROMADB_BUCKET": chromadb_bucket_name,
                "RAW_BUCKET": raw_bucket_name,
                "SITE_BUCKET": site_bucket_name,
                "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                "RECEIPT_AGENT_DYNAMO_TABLE_NAME": dynamodb_table_name,
                "OPENAI_API_KEY": openai_api_key,
                "RECEIPT_AGENT_OPENAI_API_KEY": openai_api_key,
                "RECEIPT_AGENT_OLLAMA_API_KEY": ollama_api_key,
                "RECEIPT_AGENT_OLLAMA_BASE_URL": "https://ollama.com",
                "RECEIPT_AGENT_OLLAMA_MODEL": "gpt-oss:120b-cloud",
                "LANGCHAIN_API_KEY": langchain_api_key,
                "LANGCHAIN_TRACING_V2": "true",
                "LANGCHAIN_ENDPOINT": "https://api.smith.langchain.com",
                "LANGCHAIN_PROJECT": pulumi.Config("portfolio").get(
                    "langchain_project"
                )
                or "combine-receipts",
                "PULUMI_STACK": stack,
                **(
                    {}
                    if not artifacts_bucket_name
                    else {"ARTIFACTS_BUCKET": artifacts_bucket_name}
                ),
            },
        }

        combine_docker_image = CodeBuildDockerImage(
            f"{name}-combine-img",
            dockerfile_path="infra/combine_receipts_step_functions/lambdas/Dockerfile",
            build_context_path=".",
            source_paths=[
                "receipt_dynamo",
                "receipt_chroma",
                "receipt_agent",
                "receipt_places",
                "receipt_upload",  # For image processing utilities
            ],
            lambda_function_name=f"{name}-combine-receipts",
            lambda_config=combine_lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_role]),
        )

        combine_receipts_lambda = combine_docker_image.lambda_function

        # ============================================================
        # Step Function role policy
        # ============================================================
        RolePolicy(
            f"{name}-sfn-lambda-policy",
            role=sfn_role.id,
            policy=Output.all(
                list_images_lambda.arn,
                combine_receipts_lambda.arn,
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
                list_images_lambda.arn,
                combine_receipts_lambda.arn,
                aggregate_results_lambda.arn,
                self.batch_bucket.bucket,
            ).apply(
                lambda args: self._create_step_function_definition(
                    list_images_arn=args[0],
                    combine_arn=args[1],
                    aggregate_arn=args[2],
                    batch_bucket=args[3],
                    max_concurrency=self.max_concurrency,
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
            }
        )

    def _create_step_function_definition(
        self,
        list_images_arn: str,
        combine_arn: str,
        aggregate_arn: str,
        batch_bucket: str,
        max_concurrency: int,
    ) -> str:
        """Create Step Function definition (ASL)."""
        definition = {
            "Comment": "Combine Receipts - Process images with multiple receipts",
            "StartAt": "Initialize",
            "States": {
                # Initialize state: Set up execution context
                # Simplified approach - provide defaults and let input override them
                # This avoids ResultSelector which isn't supported in current Pulumi AWS provider
                "Initialize": {
                    "Type": "Pass",
                    "Parameters": {
                        # Pass input through as-is
                        "input.$": "$",
                        # Execution context (always present)
                        "execution_id.$": "$$.Execution.Name",
                        "start_time.$": "$$.Execution.StartTime",
                        "batch_bucket": batch_bucket,
                        # Provide defaults for optional fields
                        "dry_run": False,
                        "llm_analysis_s3_key": None,
                        "limit": None,
                    },
                    "ResultPath": "$.init",
                    "Next": "ListImages",
                },
                "ListImages": {
                    "Type": "Task",
                    "Resource": list_images_arn,
                    "TimeoutSeconds": 300,
                    "Parameters": {
                        "execution_id.$": "$.init.execution_id",
                        "batch_bucket.$": "$.init.batch_bucket",
                        "llm_analysis_s3_key.$": "$.init.llm_analysis_s3_key",
                        "limit.$": "$.init.limit",
                    },
                    "ResultPath": "$.list_result",
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
                    "Next": "HasImages",
                },
                "HasImages": {
                    "Type": "Choice",
                    "Choices": [
                        {
                            "Variable": "$.list_result.images[0]",
                            "IsPresent": True,
                            "Next": "CombineReceipts",
                        }
                    ],
                    "Default": "NoImagesToProcess",
                },
                "NoImagesToProcess": {
                    "Type": "Pass",
                    "Result": {"message": "No images to process"},
                    "End": True,
                },
                "CombineReceipts": {
                    "Type": "Map",
                    "ItemsPath": "$.list_result.images",
                    "MaxConcurrency": max_concurrency,
                    "Parameters": {
                        "image_id.$": "$$.Map.Item.Value.image_id",
                        "receipt_ids.$": "$$.Map.Item.Value.receipt_ids",
                        "execution_id.$": "$.init.execution_id",
                        "batch_bucket.$": "$.init.batch_bucket",
                        "dry_run.$": "$.init.dry_run",
                        "llm_select": True,
                    },
                    "ItemProcessor": {
                        "ProcessorConfig": {"Mode": "INLINE"},
                        "StartAt": "CombineReceipt",
                        "States": {
                            "CombineReceipt": {
                                "Type": "Task",
                                "Resource": combine_arn,
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
                                    }
                                ],
                                # ResultSelector: include all fields from Lambda response
                                # We don't use ResultSelector here - let all fields pass through
                                # Optional fields will be included if present, omitted if not
                                # Removing ResultSelector allows all Lambda response fields to pass through
                                "End": True,
                            }
                        },
                    },
                    "ResultPath": "$.combine_results",
                    "Catch": [
                        {
                            "ErrorEquals": ["States.ALL"],
                            "Next": "CombineFailed",
                            "ResultPath": "$.error",
                        }
                    ],
                    "Next": "AggregateResults",
                },
                "AggregateResults": {
                    "Type": "Task",
                    "Resource": aggregate_arn,
                    "TimeoutSeconds": 120,
                    "Parameters": {
                        "execution_id.$": "$.init.execution_id",
                        "batch_bucket.$": "$.init.batch_bucket",
                        "dry_run.$": "$.init.dry_run",
                        "combine_results.$": "$.combine_results",
                    },
                    "ResultPath": "$.summary",
                    "Next": "Done",
                },
                "Done": {
                    "Type": "Pass",
                    "End": True,
                },
                "CombineFailed": {
                    "Type": "Fail",
                    "Error": "CombineReceiptsError",
                    "Cause": "Failed to combine receipts",
                },
            },
        }
        return json.dumps(definition, indent=2)
