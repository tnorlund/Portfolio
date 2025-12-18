"""Hybrid Lambda deployment component for ChromaDB compaction infrastructure.

Creates both zip-based and container-based Lambda functions following the
pattern from embedding_step_functions.
"""

# pylint: disable=duplicate-code,too-many-instance-attributes,too-many-arguments,too-many-locals
# Some duplication is expected between Lambda infrastructure components
# Lambda deployment requires many configuration parameters and component attributes

import json
from pathlib import Path
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Output, ResourceOptions

from .docker_image import DockerImageComponent
from .s3_buckets import ChromaDBBuckets
from .sqs_queues import ChromaDBQueues

try:
    from lambda_layer import (  # type: ignore[import-not-found]
        dynamo_layer,
        dynamo_stream_layer,
    )
except ImportError:
    # For testing environments, create a mock
    from unittest.mock import MagicMock

    dynamo_layer = MagicMock()
    dynamo_stream_layer = MagicMock()


class HybridLambdaDeployment(ComponentResource):
    """
    ComponentResource for hybrid Lambda deployment.

    Creates:
    - Zip-based Lambda for stream processing (lightweight)
    - Container-based Lambda for enhanced compaction (complex ChromaDB
      operations)
    - Shared IAM roles and policies
    - Event source mappings for both functions
    """

    # pylint: disable=too-many-positional-arguments
    # This component requires many parameters for proper configuration
    def __init__(
        self,
        name: str,
        chromadb_queues: ChromaDBQueues,
        chromadb_buckets: ChromaDBBuckets,
        dynamodb_table_arn: str,
        dynamodb_stream_arn: str,
        vpc_subnet_ids=None,
        lambda_security_group_id: str | None = None,
        efs_access_point_arn: str | None = None,
        storage_mode: str = "auto",
        stack: Optional[str] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        """
        Initialize the Hybrid Lambda Deployment.

        Args:
            name: The unique name of the resource
            chromadb_queues: The ChromaDB SQS queues component
            chromadb_buckets: The ChromaDB S3 buckets component
            dynamodb_table_arn: ARN of the DynamoDB table
            dynamodb_stream_arn: ARN of the DynamoDB stream
            stack: The Pulumi stack name (defaults to current stack)
            opts: Optional resource options
        """
        super().__init__("chromadb:compaction:HybridLambda", name, None, opts)

        # Get stack
        if stack is None:
            stack = pulumi.get_stack()

        # Create Docker image component for container-based Lambda
        # Note: Lambda config will be passed after we create the role
        self.docker_image = None  # Will be created after role is set up

        # Create shared IAM role for both Lambda functions
        self.lambda_role = aws.iam.Role(
            f"{name}-lambda-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Action": "sts:AssumeRole",
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                        }
                    ],
                }
            ),
            tags={
                "Project": "ChromaDB",
                "Component": "HybridCompaction",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Attach basic Lambda execution policy
        aws.iam.RolePolicyAttachment(
            f"{name}-lambda-basic-execution",
            role=self.lambda_role.name,
            # pylint: disable=line-too-long
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self),
        )

        # Attach VPC access policy if we will attach VPC config later
        if vpc_subnet_ids and lambda_security_group_id:
            aws.iam.RolePolicyAttachment(
                f"{name}-lambda-vpc-access",
                role=self.lambda_role.name,
                policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
                opts=ResourceOptions(parent=self),
            )

        # Create shared policies
        self._create_shared_policies(
            name, dynamodb_table_arn, chromadb_queues, chromadb_buckets
        )

        # Normalize and validate storage mode once
        normalized_storage_mode = storage_mode.lower()
        if normalized_storage_mode not in {"auto", "s3", "efs"}:
            raise ValueError(
                f"Invalid storage_mode='{storage_mode}'. "
                "Expected one of: 'auto', 's3', or 'efs'."
            )

        # Decide whether to mount EFS based on access point and mode
        use_efs_mount = (
            efs_access_point_arn is not None and normalized_storage_mode != "s3"
        )

        # Validate storage_mode='efs' requires EFS access point
        if normalized_storage_mode == "efs" and not use_efs_mount:
            raise ValueError(
                "storage_mode='efs' requires efs_access_point_arn to be provided. "
                "Cannot set CHROMADB_STORAGE_MODE='efs' without an EFS access point."
            )

        self.docker_image = DockerImageComponent(
            f"{name}-docker",
            lambda_config={
                "role_arn": self.lambda_role.arn,
                # Increased timeout from 300s to 900s (15 min) based on log analysis:
                # - Multiple timeouts at 300s with operations still running
                # - Snapshot operations (400-550MB) require more time
                # - Evidence shows operations taking 300-516 seconds
                "timeout": 900,  # 15 minutes to handle large snapshot operations
                # Increased memory from 2048MB to 4096MB (4GB) based on log analysis:
                # - Multiple failures showing "Max Memory Used: 2048 MB" (hitting limit)
                # - Snapshot uploads average 446MB, largest 552MB
                # - Need headroom for runtime + ChromaDB + snapshot operations
                "memory_size": 4096,  # 4GB to handle large snapshots without OOM kills
                # Increased ephemeral storage from 5GB to 10GB for large snapshot operations
                "ephemeral_storage": 10240,  # 10GB for ChromaDB snapshots (largest seen: 552MB)
                "reserved_concurrent_executions": 10,  # Prevent throttling
                "description": (
                    "Enhanced ChromaDB compaction handler for stream and "
                    "delta message processing"
                ),
                "tags": {
                    "Project": "ChromaDB",
                    "Component": "EnhancedCompaction",
                    "Environment": stack,
                    "ManagedBy": "Pulumi",
                },
                "environment": {
                    "DYNAMODB_TABLE_NAME": Output.all(dynamodb_table_arn).apply(
                        lambda args: args[0].split("/")[-1]
                    ),
                    "CHROMADB_BUCKET": chromadb_buckets.bucket_name,
                    "LINES_QUEUE_URL": chromadb_queues.lines_queue_url,
                    "WORDS_QUEUE_URL": chromadb_queues.words_queue_url,
                    "HEARTBEAT_INTERVAL_SECONDS": "30",
                    "LOCK_DURATION_MINUTES": "3",
                    "MAX_HEARTBEAT_FAILURES": "3",
                    "LOG_LEVEL": "INFO",
                    "CHROMA_ROOT": (
                        "/mnt/chroma" if use_efs_mount else "/tmp/chroma"  # noqa: S108
                    ),
                    # Storage mode configuration: "auto", "s3", or "efs"
                    # - "auto": Use EFS if available, fallback to S3
                    # - "s3": Force S3-only mode (ignore EFS)
                    # - "efs": Force EFS mode (fail if EFS not available)
                    "CHROMADB_STORAGE_MODE": normalized_storage_mode,
                    # Enable custom CloudWatch metrics now that Lambda has internet
                    # access via NAT instance. If timeouts occur, consider adding a
                    # CloudWatch Metrics Interface VPC Endpoint (~$7/month).
                    "ENABLE_METRICS": "true",
                },
                "vpc_config": {
                    "subnet_ids": vpc_subnet_ids,
                    "security_group_ids": [lambda_security_group_id],
                },
                # EFS mount enabled for networking
                "file_system_config": (
                    {
                        "arn": efs_access_point_arn,
                        "local_mount_path": "/mnt/chroma",
                    }
                    if use_efs_mount
                    else None
                ),
            },
            opts=ResourceOptions(parent=self, depends_on=[self.lambda_role]),
        )

        # Create CloudWatch log groups (auto-generated names)
        self.stream_log_group = aws.cloudwatch.LogGroup(
            f"{name}-stream-log-group",
            retention_in_days=14,
            tags={
                "Project": "ChromaDB",
                "Component": "StreamProcessor",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        self.compaction_log_group = aws.cloudwatch.LogGroup(
            f"{name}-compaction-log-group",
            retention_in_days=14,
            tags={
                "Project": "ChromaDB",
                "Component": "EnhancedCompaction",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Optional VPC configuration
        vpc_cfg = (
            aws.lambda_.FunctionVpcConfigArgs(
                subnet_ids=vpc_subnet_ids,
                security_group_ids=[lambda_security_group_id],
            )
            if vpc_subnet_ids and lambda_security_group_id
            else None
        )

        # Create zip-based Lambda for stream processing
        self.stream_processor_function = aws.lambda_.Function(
            f"{name}-stream-processor",
            runtime="python3.12",
            architectures=["arm64"],
            code=pulumi.AssetArchive(
                {
                    "stream_processor.py": pulumi.FileAsset(
                        str(
                            Path(__file__).parent.parent
                            / "lambdas"
                            / "stream_processor.py"
                        )
                    ),
                    # Ensure utils are packaged
                    "utils/__init__.py": pulumi.FileAsset(
                        str(
                            Path(__file__).parent.parent
                            / "lambdas"
                            / "utils"
                            / "__init__.py"
                        )
                    ),
                    "utils/aws_clients.py": pulumi.FileAsset(
                        str(
                            Path(__file__).parent.parent
                            / "lambdas"
                            / "utils"
                            / "aws_clients.py"
                        )
                    ),
                    "utils/logging.py": pulumi.FileAsset(
                        str(
                            Path(__file__).parent.parent
                            / "lambdas"
                            / "utils"
                            / "logging.py"
                        )
                    ),
                    "utils/metrics.py": pulumi.FileAsset(
                        str(
                            Path(__file__).parent.parent
                            / "lambdas"
                            / "utils"
                            / "metrics.py"
                        )
                    ),
                    "utils/response.py": pulumi.FileAsset(
                        str(
                            Path(__file__).parent.parent
                            / "lambdas"
                            / "utils"
                            / "response.py"
                        )
                    ),
                    "utils/timeout_handler.py": pulumi.FileAsset(
                        str(
                            Path(__file__).parent.parent
                            / "lambdas"
                            / "utils"
                            / "timeout_handler.py"
                        )
                    ),
                    "utils/tracing.py": pulumi.FileAsset(
                        str(
                            Path(__file__).parent.parent
                            / "lambdas"
                            / "utils"
                            / "tracing.py"
                        )
                    ),
                }
            ),
            handler="stream_processor.lambda_handler",
            role=self.lambda_role.arn,
            timeout=120,  # 2 minutes timeout (reduced from 5 to prevent long hangs)
            memory_size=256,  # Lightweight processing
            # Removed reserved_concurrent_executions to allow parallel processing
            # FIFO queue MessageGroupId already ensures proper ordering per image
            # Stream processor can safely process multiple images in parallel
            # No VPC config - stream processor only needs AWS service access (DynamoDB, SQS, CloudWatch)
            environment={
                "variables": {
                    "LINES_QUEUE_URL": chromadb_queues.lines_queue_url,
                    "WORDS_QUEUE_URL": chromadb_queues.words_queue_url,
                    "LOG_LEVEL": "INFO",
                    # Stream processing configuration (aligned with code and infrastructure)
                    "MAX_RECORDS_PER_INVOCATION": "10",  # Matches DynamoDB Stream batch_size
                    "LAMBDA_TIMEOUT_SECONDS": "120",  # Matches Lambda timeout setting
                    "MAX_CONSECUTIVE_FAILURES": "10",  # Circuit breaker threshold
                }
            },
            description=(
                "Processes DynamoDB stream events for ChromaDB metadata "
                "synchronization"
            ),
            tags={
                "Project": "ChromaDB",
                "Component": "StreamProcessor",
                "Environment": stack,
                "ManagedBy": "Pulumi",
                # Required for the layer updater to auto-attach new versions
                "environment": stack,
            },
            layers=[dynamo_layer.arn, dynamo_stream_layer.arn],
            opts=ResourceOptions(
                parent=self,
                depends_on=[
                    self.lambda_role,
                    self.stream_log_group,
                ],
                ignore_changes=["layers"],
            ),
        )

        # VPC and EFS configuration is now handled in lambda_config

        # Use the Lambda function created by DockerImageComponent
        self.enhanced_compaction_function = (
            self.docker_image.docker_image.lambda_function
        )

        # Create event source mappings
        self._create_event_source_mappings(name, dynamodb_stream_arn, chromadb_queues)

        # Diagnostic EFS listing Lambda (zip-based, same role/VPC/EFS)
        diag_code = pulumi.AssetArchive(
            {
                "handler.py": pulumi.FileAsset(
                    str(
                        Path(__file__).parent.parent
                        / "lambdas"
                        / "efs_diag"
                        / "handler.py"
                    )
                )
            }
        )

        self.efs_diag_function = aws.lambda_.Function(
            f"{name}-efs-diag",
            runtime="python3.12",
            architectures=["arm64"],
            code=diag_code,
            handler="handler.lambda_handler",
            role=self.lambda_role.arn,
            timeout=30,
            memory_size=256,
            environment={
                "variables": {
                    "CHROMA_ROOT": (
                        "/mnt/chroma" if use_efs_mount else "/tmp/chroma"  # noqa: S108
                    ),
                }
            },
            vpc_config=(
                aws.lambda_.FunctionVpcConfigArgs(
                    subnet_ids=vpc_subnet_ids,
                    security_group_ids=[lambda_security_group_id],
                )
                if vpc_subnet_ids and lambda_security_group_id
                else None
            ),
            file_system_config=(
                aws.lambda_.FunctionFileSystemConfigArgs(
                    arn=efs_access_point_arn,
                    local_mount_path="/mnt/chroma",
                )
                if use_efs_mount
                else None
            ),
            opts=ResourceOptions(parent=self, depends_on=[self.lambda_role]),
        )

        # Export useful properties
        self.stream_processor_arn = self.stream_processor_function.arn
        self.enhanced_compaction_arn = self.enhanced_compaction_function.arn
        self.role_arn = self.lambda_role.arn

        # Register outputs
        self.register_outputs(
            {
                "stream_processor_arn": self.stream_processor_arn,
                "enhanced_compaction_arn": self.enhanced_compaction_arn,
                "role_arn": self.role_arn,
                "docker_image_uri": self.docker_image.image_uri,
                "efs_diag_arn": self.efs_diag_function.arn,
            }
        )

    def _create_shared_policies(
        self,
        name: str,
        dynamodb_table_arn: str,
        chromadb_queues: ChromaDBQueues,
        chromadb_buckets: ChromaDBBuckets,
    ):
        """Create shared IAM policies for both Lambda functions."""

        # DynamoDB access policy (for both stream reading and table operations)
        self.dynamodb_policy = aws.iam.RolePolicy(
            f"{name}-dynamodb-policy",
            role=self.lambda_role.id,
            policy=Output.all(dynamodb_table_arn).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:DescribeStream",
                                    "dynamodb:GetRecords",
                                    "dynamodb:GetShardIterator",
                                    "dynamodb:ListStreams",
                                ],
                                "Resource": [
                                    args[0],  # Table ARN
                                    # Stream ARN pattern
                                    f"{args[0]}/stream/*",
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:GetItem",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:DeleteItem",
                                    "dynamodb:Query",
                                    "dynamodb:DescribeTable",
                                ],
                                "Resource": [
                                    args[0],  # Table ARN
                                    f"{args[0]}/index/*",  # GSI ARNs
                                ],
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # S3 access policy
        self.s3_policy = aws.iam.RolePolicy(
            f"{name}-s3-policy",
            role=self.lambda_role.id,
            policy=Output.all(chromadb_buckets.bucket_arn).apply(
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
                                    args[0],  # Bucket ARN
                                    f"{args[0]}/*",  # Objects in bucket
                                ],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # SQS access policy
        self.sqs_policy = aws.iam.RolePolicy(
            f"{name}-sqs-policy",
            role=self.lambda_role.id,
            policy=Output.all(
                chromadb_queues.lines_queue_arn,
                chromadb_queues.words_queue_arn,
                chromadb_queues.lines_dlq_arn,
                chromadb_queues.words_dlq_arn,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "sqs:SendMessage",
                                    "sqs:SendMessageBatch",
                                    "sqs:ReceiveMessage",
                                    "sqs:DeleteMessage",
                                    "sqs:GetQueueAttributes",
                                ],
                                "Resource": [
                                    args[0],  # Lines queue ARN
                                    args[1],  # Words queue ARN
                                    args[2],  # Lines DLQ ARN
                                    args[3],  # Words DLQ ARN
                                ],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # CloudWatch metrics policy for observability
        self.cloudwatch_policy = aws.iam.RolePolicy(
            f"{name}-cloudwatch-policy",
            role=self.lambda_role.id,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "cloudwatch:PutMetricData",
                                "logs:CreateLogGroup",
                                "logs:CreateLogStream",
                                "logs:PutLogEvents",
                            ],
                            "Resource": "*",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # ECR policy for container image Lambda functions
        # Lambda service needs to pull images from ECR when code is updated
        self.ecr_policy = aws.iam.RolePolicy(
            f"{name}-ecr-policy",
            role=self.lambda_role.id,
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
            opts=ResourceOptions(parent=self),
        )

    def _create_event_source_mappings(
        self,
        name: str,
        dynamodb_stream_arn: str,
        chromadb_queues: ChromaDBQueues,
    ):
        """Create event source mappings for both Lambda functions."""

        # DynamoDB stream to stream processor
        self.stream_event_source_mapping = aws.lambda_.EventSourceMapping(
            f"{name}-stream-event-source-mapping",
            event_source_arn=dynamodb_stream_arn,
            function_name=self.stream_processor_function.arn,
            starting_position="LATEST",
            batch_size=10,  # Max batch size for DynamoDB streams
            maximum_batching_window_in_seconds=5,  # Batch more records per invocation (up to 5 seconds)
            parallelization_factor=5,  # Process up to 5 shards in parallel (increases throughput)
            maximum_retry_attempts=3,
            maximum_record_age_in_seconds=3600,
            bisect_batch_on_function_error=True,
            opts=ResourceOptions(parent=self),
        )

        # SQS queues to enhanced compaction handler
        self.lines_event_source_mapping = aws.lambda_.EventSourceMapping(
            f"{name}-lines-event-source-mapping",
            event_source_arn=chromadb_queues.lines_queue_arn,
            function_name=self.enhanced_compaction_function.arn,
            batch_size=10,  # FIFO queues support batch size up to 10
            function_response_types=["ReportBatchItemFailures"],
            opts=ResourceOptions(parent=self),
        )

        self.words_event_source_mapping = aws.lambda_.EventSourceMapping(
            f"{name}-words-event-source-mapping",
            event_source_arn=chromadb_queues.words_queue_arn,
            function_name=self.enhanced_compaction_function.arn,
            batch_size=10,  # FIFO queues support batch size up to 10
            function_response_types=["ReportBatchItemFailures"],
            opts=ResourceOptions(parent=self),
        )


# pylint: disable=too-many-positional-arguments
# Factory functions often require many parameters
def create_hybrid_lambda_deployment(
    name: str = "chromadb-hybrid-compaction",
    chromadb_queues: ChromaDBQueues = None,
    chromadb_buckets: ChromaDBBuckets = None,
    dynamodb_table_arn: str = None,
    dynamodb_stream_arn: str = None,
    vpc_subnet_ids=None,
    lambda_security_group_id: str | None = None,
    efs_access_point_arn: str | None = None,
    storage_mode: str = "auto",
    opts: Optional[ResourceOptions] = None,
) -> HybridLambdaDeployment:
    """
    Factory function to create the hybrid Lambda deployment.

    Args:
        name: Base name for the resources
        chromadb_queues: The ChromaDB SQS queues component
        chromadb_buckets: The ChromaDB S3 buckets component
        dynamodb_table_arn: ARN of the DynamoDB table
        dynamodb_stream_arn: ARN of the DynamoDB stream
        opts: Optional resource options

    Returns:
        HybridLambdaDeployment component
    """
    if not chromadb_queues:
        raise ValueError("chromadb_queues parameter is required")
    if not chromadb_buckets:
        raise ValueError("chromadb_buckets parameter is required")
    if not dynamodb_table_arn:
        raise ValueError("dynamodb_table_arn parameter is required")
    if not dynamodb_stream_arn:
        raise ValueError("dynamodb_stream_arn parameter is required")

    return HybridLambdaDeployment(
        name=name,
        chromadb_queues=chromadb_queues,
        chromadb_buckets=chromadb_buckets,
        dynamodb_table_arn=dynamodb_table_arn,
        dynamodb_stream_arn=dynamodb_stream_arn,
        vpc_subnet_ids=vpc_subnet_ids,
        lambda_security_group_id=lambda_security_group_id,
        efs_access_point_arn=efs_access_point_arn,
        storage_mode=storage_mode,
        opts=opts,
    )
