"""
Pulumi infrastructure for Metadata Harmonizer Step Function.

This component creates a Step Function that:
1. Lists unique place_ids from DynamoDB (zip Lambda)
2. Processes place_id groups in batches (container Lambda with LLM agent)
3. Aggregates results

Architecture:
- Zip Lambda: list_place_ids (DynamoDB layer only, fast startup)
- Container Lambda: harmonize_metadata (LLM agent, heavy processing)
- S3 Bucket: batch files and results storage (optional, for future use)
- Step Function: orchestration with Map state for parallel processing
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
from pulumi_aws.cloudwatch import LogGroup, MetricAlarm
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
google_places_api_key = config.get_secret("GOOGLE_PLACES_API_KEY")  # Optional

# Metadata harmonizer specific config
harmonizer_config = Config("metadata-harmonizer")
max_concurrency_default = harmonizer_config.get_int("max_concurrency") or 5
batch_size_default = harmonizer_config.get_int("batch_size") or 10
max_receipts_per_batch_default = (
    harmonizer_config.get_int("max_receipts_per_batch") or 20
)


class MetadataHarmonizerStepFunction(ComponentResource):
    """
    Step Function infrastructure for metadata harmonization.

    Components:
    - Zip Lambda: list_place_ids (DynamoDB layer only, fast)
    - Container Lambda: harmonize_metadata (LLM agent)
    - S3 Bucket: batch files and results (optional)
    - Step Function: orchestration with Map state

    Workflow:
    1. ListPlaceIds - Query DynamoDB for unique place_ids
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
        max_receipts_per_batch: Optional[int] = None,
        base_image_uri: Optional[pulumi.Input[str]] = None,  # Optional base image for faster builds
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(f"{__name__}-{name}", name, None, opts)
        stack = pulumi.get_stack()
        self._name = name  # Store name for use in alarm creation

        # Use provided values or fall back to config or defaults
        self.max_concurrency = max_concurrency or max_concurrency_default
        self.batch_size = batch_size or batch_size_default
        self.max_receipts_per_batch = (
            max_receipts_per_batch or max_receipts_per_batch_default
        )

        # ============================================================
        # S3 Bucket for batch files and results
        # ============================================================
        self.batch_bucket = Bucket(
            f"{name}-batch-bucket",
            force_destroy=True,
            tags={
                "environment": stack,
                "purpose": "metadata-harmonizer-batches",
            },
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
        dynamodb_policy = RolePolicy(
            f"{name}-lambda-dynamo-policy",
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
                                "Resource": [
                                    arn,
                                    f"{arn}/index/*",
                                ],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # S3 access policy (batch bucket + ChromaDB bucket if provided)
        if chromadb_bucket_arn:
            s3_policy = Output.all(
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
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:ListBucket",
                                ],
                                "Resource": [
                                    args[1],
                                    f"{args[1]}/*",
                                ],
                            },
                        ],
                    }
                )
            )
        else:
            s3_policy = self.batch_bucket.arn.apply(
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
                                "Resource": [
                                    arn,
                                    f"{arn}/*",
                                ],
                            }
                        ],
                    }
                )
            )

        RolePolicy(
            f"{name}-lambda-s3-policy",
            role=lambda_role.id,
            policy=s3_policy,
            opts=ResourceOptions(parent=lambda_role),
        )

        # ============================================================
        # Zip Lambda: list_place_ids
        # ============================================================
        CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
        HANDLERS_DIR = os.path.join(CURRENT_DIR, "handlers")

        list_place_ids_lambda = Function(
            f"{name}-list-place-ids",
            name=f"{name}-list-place-ids",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="list_place_ids.handler",
            code=AssetArchive(
                {
                    "list_place_ids.py": FileAsset(
                        os.path.join(HANDLERS_DIR, "list_place_ids.py")
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

        # ============================================================
        # Container Lambda: harmonize_metadata
        # ============================================================
        # Note: receipt_agent uses RECEIPT_AGENT_* prefixed env vars
        # via pydantic-settings. See receipt_agent/config/settings.py
        harmonize_lambda_config = {
            "role_arn": lambda_role.arn,
            "timeout": 900,  # 15 minutes
            "memory_size": 3072,  # 3 GB for LLM agent
            "tags": {"environment": stack},
            "ephemeral_storage": 10240,  # 10 GB /tmp (may be needed for large data processing and ChromaDB snapshots)
            "environment": {
                # Lambda-specific
                "BATCH_BUCKET": self.batch_bucket.bucket,
                # ChromaDB (for metadata finder sub-agent)
                "CHROMADB_BUCKET": chromadb_bucket_name or "",
                # receipt_agent Settings (RECEIPT_AGENT_* prefix)
                "RECEIPT_AGENT_DYNAMO_TABLE_NAME": dynamodb_table_name,
                "RECEIPT_AGENT_OPENAI_API_KEY": openai_api_key,
                "RECEIPT_AGENT_OLLAMA_API_KEY": ollama_api_key,
                "RECEIPT_AGENT_OLLAMA_BASE_URL": "https://ollama.com",
                "RECEIPT_AGENT_OLLAMA_MODEL": "gpt-oss:120b-cloud",
                # ChromaDB directory (will be set when downloading snapshots)
                "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY": "/tmp/chromadb",
                # Google Places (optional, but enabled by default)
                "GOOGLE_PLACES_API_KEY": google_places_api_key,
                "RECEIPT_PLACES_TABLE_NAME": dynamodb_table_name,
                "RECEIPT_PLACES_AWS_REGION": "us-east-1",  # Table is in us-east-1
                # LangSmith tracing (enabled for debugging)
                "LANGCHAIN_API_KEY": langchain_api_key,
                "LANGCHAIN_TRACING_V2": "true",
                "LANGCHAIN_ENDPOINT": "https://api.smith.langchain.com",
                "LANGCHAIN_PROJECT": pulumi.Config("portfolio").get(
                    "langchain_project"
                )
                or "metadata-harmonizer",
            },
        }

        # Use base image if provided for faster builds and smaller S3 uploads
        dockerfile_to_use = (
            "infra/metadata_harmonizer_step_functions/lambdas/Dockerfile.with_base"
            if base_image_uri
            else "infra/metadata_harmonizer_step_functions/lambdas/Dockerfile"
        )

        # When using base image, only include receipt_agent (base has everything else)
        # When not using base image, include all dependencies
        source_paths_to_use = (
            ["receipt_agent"]  # Only receipt_agent when using base image
            if base_image_uri
            else [
                "receipt_agent",
                "receipt_places",
                "receipt_upload",
            ]
        )

        harmonize_docker_image = CodeBuildDockerImage(
            f"{name}-harmonize-img",
            dockerfile_path=dockerfile_to_use,
            build_context_path=".",
            source_paths=source_paths_to_use,
            lambda_function_name=f"{name}-harmonize-metadata",
            lambda_config=harmonize_lambda_config,
            platform="linux/arm64",
            base_image_uri=base_image_uri,  # Pass base image URI for optimization
            opts=ResourceOptions(
                parent=self, depends_on=[lambda_role, dynamodb_policy]
            ),
        )

        harmonize_metadata_lambda = harmonize_docker_image.lambda_function

        # ============================================================
        # Step Function role policy
        # ============================================================
        RolePolicy(
            f"{name}-sfn-lambda-policy",
            role=sfn_role.id,
            policy=Output.all(
                list_place_ids_lambda.arn,
                harmonize_metadata_lambda.arn,
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
                list_place_ids_lambda.arn,
                harmonize_metadata_lambda.arn,
                self.batch_bucket.bucket,
            ).apply(
                lambda args: self._create_step_function_definition(
                    list_place_ids_arn=args[0],
                    harmonize_arn=args[1],
                    batch_bucket=args[2],
                    max_concurrency=self.max_concurrency,
                    batch_size=self.batch_size,
                    max_receipts_per_batch=self.max_receipts_per_batch,
                )
            ),
            logging_configuration=logging_config,
            opts=ResourceOptions(parent=self, depends_on=[log_group]),
        )

        # ============================================================
        # CloudWatch Alarms
        # ============================================================
        # Create alarms after state machine is created (depends on it)
        self._create_cloudwatch_alarms(
            list_place_ids_lambda=list_place_ids_lambda,
            harmonize_metadata_lambda=harmonize_metadata_lambda,
            opts=ResourceOptions(parent=self, depends_on=[self.state_machine]),
        )

        # ============================================================
        # Outputs
        # ============================================================
        self.state_machine_arn = self.state_machine.arn
        self.batch_bucket_name = self.batch_bucket.bucket
        self.list_place_ids_lambda_arn = list_place_ids_lambda.arn
        self.harmonize_metadata_lambda_arn = harmonize_metadata_lambda.arn

        self.register_outputs(
            {
                "state_machine_arn": self.state_machine.arn,
                "batch_bucket_name": self.batch_bucket.bucket,
                "list_place_ids_lambda_arn": list_place_ids_lambda.arn,
                "harmonize_metadata_lambda_arn": harmonize_metadata_lambda.arn,
            }
        )

    def _create_cloudwatch_alarms(
        self,
        list_place_ids_lambda: Function,
        harmonize_metadata_lambda: Function,
        opts: Optional[ResourceOptions] = None,
    ):
        """Create CloudWatch alarms for Lambda functions and Step Function."""
        stack = pulumi.get_stack()

        # Lambda alarms for list_place_ids
        MetricAlarm(
            f"{self._name}-list-place-ids-timeout-alarm",
            alarm_description=f"Lambda {self._name}-list-place-ids approaching timeout",
            metric_name="Duration",
            namespace="AWS/Lambda",
            statistic="Maximum",
            period=300,  # 5 minutes
            evaluation_periods=1,
            threshold=list_place_ids_lambda.timeout.apply(
                lambda t: t * 1000 * 0.8
            ),  # 80% of timeout in ms
            comparison_operator="GreaterThanThreshold",
            dimensions={"FunctionName": list_place_ids_lambda.name},
            treat_missing_data="notBreaching",
            tags={"environment": stack},
            opts=ResourceOptions(parent=self) if opts is None else opts,
        )

        MetricAlarm(
            f"{self._name}-list-place-ids-error-alarm",
            alarm_description=f"Lambda {self._name}-list-place-ids high error rate",
            metric_name="Errors",
            namespace="AWS/Lambda",
            statistic="Sum",
            period=300,  # 5 minutes
            evaluation_periods=2,
            threshold=1,  # Any error
            comparison_operator="GreaterThanOrEqualToThreshold",
            dimensions={"FunctionName": list_place_ids_lambda.name},
            treat_missing_data="notBreaching",
            tags={"environment": stack},
            opts=ResourceOptions(parent=self) if opts is None else opts,
        )

        MetricAlarm(
            f"{self._name}-list-place-ids-throttle-alarm",
            alarm_description=f"Lambda {self._name}-list-place-ids being throttled",
            metric_name="Throttles",
            namespace="AWS/Lambda",
            statistic="Sum",
            period=300,  # 5 minutes
            evaluation_periods=1,
            threshold=1,  # Any throttling
            comparison_operator="GreaterThanOrEqualToThreshold",
            dimensions={"FunctionName": list_place_ids_lambda.name},
            treat_missing_data="notBreaching",
            tags={"environment": stack},
            opts=ResourceOptions(parent=self) if opts is None else opts,
        )

        # Lambda alarms for harmonize_metadata
        MetricAlarm(
            f"{self._name}-harmonize-timeout-alarm",
            alarm_description=f"Lambda {self._name}-harmonize-metadata approaching timeout",
            metric_name="Duration",
            namespace="AWS/Lambda",
            statistic="Maximum",
            period=300,  # 5 minutes
            evaluation_periods=1,
            threshold=harmonize_metadata_lambda.timeout.apply(
                lambda t: t * 1000 * 0.8
            ),  # 80% of timeout in ms
            comparison_operator="GreaterThanThreshold",
            dimensions={"FunctionName": harmonize_metadata_lambda.name},
            treat_missing_data="notBreaching",
            tags={"environment": stack},
            opts=ResourceOptions(parent=self) if opts is None else opts,
        )

        MetricAlarm(
            f"{self._name}-harmonize-error-alarm",
            alarm_description=f"Lambda {self._name}-harmonize-metadata high error rate",
            metric_name="Errors",
            namespace="AWS/Lambda",
            statistic="Sum",
            period=300,  # 5 minutes
            evaluation_periods=2,
            threshold=1,  # Any error
            comparison_operator="GreaterThanOrEqualToThreshold",
            dimensions={"FunctionName": harmonize_metadata_lambda.name},
            treat_missing_data="notBreaching",
            tags={"environment": stack},
            opts=ResourceOptions(parent=self) if opts is None else opts,
        )

        MetricAlarm(
            f"{self._name}-harmonize-throttle-alarm",
            alarm_description=f"Lambda {self._name}-harmonize-metadata being throttled",
            metric_name="Throttles",
            namespace="AWS/Lambda",
            statistic="Sum",
            period=300,  # 5 minutes
            evaluation_periods=1,
            threshold=1,  # Any throttling
            comparison_operator="GreaterThanOrEqualToThreshold",
            dimensions={"FunctionName": harmonize_metadata_lambda.name},
            treat_missing_data="notBreaching",
            tags={"environment": stack},
            opts=ResourceOptions(parent=self) if opts is None else opts,
        )

        # Step Function execution failure alarm
        MetricAlarm(
            f"{self._name}-sf-execution-failure-alarm",
            alarm_description=f"Step Function {self._name}-sf execution failures",
            metric_name="ExecutionsFailed",
            namespace="AWS/States",
            statistic="Sum",
            period=300,  # 5 minutes
            evaluation_periods=1,
            threshold=1,  # Any failure
            comparison_operator="GreaterThanOrEqualToThreshold",
            dimensions={"StateMachineArn": self.state_machine.arn},
            treat_missing_data="notBreaching",
            tags={"environment": stack},
            opts=ResourceOptions(parent=self) if opts is None else opts,
        )

        # Step Function execution timeout alarm (30 minutes)
        MetricAlarm(
            f"{self._name}-sf-execution-timeout-alarm",
            alarm_description=f"Step Function {self._name}-sf execution timeouts",
            metric_name="ExecutionTime",
            namespace="AWS/States",
            statistic="Maximum",
            period=300,  # 5 minutes
            evaluation_periods=1,
            threshold=1800000,  # 30 minutes in milliseconds
            comparison_operator="GreaterThanThreshold",
            dimensions={"StateMachineArn": self.state_machine.arn},
            treat_missing_data="notBreaching",
            tags={"environment": stack},
            opts=ResourceOptions(parent=self) if opts is None else opts,
        )

    def _create_step_function_definition(
        self,
        list_place_ids_arn: str,
        harmonize_arn: str,
        batch_bucket: str,
        max_concurrency: int,
        batch_size: int,
        max_receipts_per_batch: int,
    ) -> str:
        """Create Step Function definition (ASL)."""
        definition = {
            "Comment": "Metadata Harmonizer - Process place_id groups in parallel",
            "StartAt": "Initialize",
            "States": {
                # Initialize execution context
                "Initialize": {
                    "Type": "Pass",
                    "Parameters": {
                        "execution_id.$": "$$.Execution.Name",
                        "start_time.$": "$$.Execution.StartTime",
                        "dry_run.$": "$.dry_run",
                        "batch_bucket": batch_bucket,
                        "batch_size": batch_size,
                        "max_receipts_per_batch": max_receipts_per_batch,
                        "langchain_project.$": "$.langchain_project",
                    },
                    "ResultPath": "$.init",
                    "Next": "ListPlaceIds",
                },
                # List unique place_ids from DynamoDB
                "ListPlaceIds": {
                    "Type": "Task",
                    "Resource": list_place_ids_arn,
                    "TimeoutSeconds": 300,
                    "Parameters": {
                        "execution_id.$": "$.init.execution_id",
                        "batch_bucket.$": "$.init.batch_bucket",
                        "batch_size.$": "$.init.batch_size",
                        "max_receipts_per_batch.$": "$.init.max_receipts_per_batch",
                    },
                    "ResultPath": "$.place_ids_data",
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
                    "Next": "HasPlaceIds",
                },
                # Check if there are place_ids to process
                "HasPlaceIds": {
                    "Type": "Choice",
                    "Choices": [
                        {
                            "Variable": "$.place_ids_data.place_id_batches[0]",
                            "IsPresent": True,
                            "Next": "ProcessInBatches",
                        }
                    ],
                    "Default": "NoPlaceIdsToProcess",
                },
                "NoPlaceIdsToProcess": {
                    "Type": "Pass",
                    "Result": {"message": "No place_ids to process"},
                    "End": True,
                },
                # Process place_id batches in parallel
                "ProcessInBatches": {
                    "Type": "Map",
                    "ItemsPath": "$.place_ids_data.place_id_batches",
                    "MaxConcurrency": max_concurrency,
                    "Parameters": {
                        "place_ids.$": "$$.Map.Item.Value",
                        "execution_id.$": "$.init.execution_id",
                        "dry_run.$": "$.init.dry_run",
                        "langchain_project.$": "$.init.langchain_project",
                        "batch_bucket.$": "$.init.batch_bucket",
                        "max_receipts_per_batch.$": "$.place_ids_data.max_receipts_per_batch",
                    },
                    "ItemProcessor": {
                        "ProcessorConfig": {"Mode": "INLINE"},
                        "StartAt": "HarmonizeMetadata",
                        "States": {
                            "HarmonizeMetadata": {
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
                                "ResultSelector": {
                                    "status.$": "$.status",
                                    "place_ids.$": "$.place_ids",
                                    "groups_processed.$": "$.groups_processed",
                                    "receipts_updated.$": "$.receipts_updated",
                                    "receipts_failed.$": "$.receipts_failed",
                                    "total_receipts.$": "$.total_receipts",
                                    "results_path.$": "$.results_path",
                                },
                                "End": True,
                            }
                        },
                    },
                    "ResultSelector": {
                        "batch_count.$": "States.ArrayLength($)",
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
                # Success state
                "Done": {
                    "Type": "Pass",
                    "End": True,
                },
                # Error state
                "ProcessFailed": {
                    "Type": "Fail",
                    "Error": "ProcessMetadataError",
                    "Cause": "Failed to process metadata harmonization",
                },
            },
        }

        return json.dumps(definition)
