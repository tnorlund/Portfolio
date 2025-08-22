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

from .sqs_queues import ChromaDBQueues
from .s3_buckets import ChromaDBBuckets
from .docker_image import DockerImageComponent

try:
    from lambda_layer import dynamo_layer  # type: ignore[import-not-found]
except ImportError:
    # For testing environments, create a mock
    from unittest.mock import MagicMock

    dynamo_layer = MagicMock()


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
        base_images=None,
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
            base_images: Base images for container builds
            stack: The Pulumi stack name (defaults to current stack)
            opts: Optional resource options
        """
        super().__init__("chromadb:compaction:HybridLambda", name, None, opts)

        # Get stack
        if stack is None:
            stack = pulumi.get_stack()

        # Create Docker image component for container-based Lambda
        self.docker_image = DockerImageComponent(
            f"{name}-docker",
            base_images=base_images,
            opts=ResourceOptions(parent=self),
        )

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

        # Create shared policies
        self._create_shared_policies(
            name, dynamodb_table_arn, chromadb_queues, chromadb_buckets
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
                    "utils/__init__.py": pulumi.FileAsset(
                        str(
                            Path(__file__).parent.parent
                            / "utils"
                            / "__init__.py"
                        )
                    ),
                    "utils/logging.py": pulumi.FileAsset(
                        str(
                            Path(__file__).parent.parent
                            / "utils"
                            / "logging.py"
                        )
                    ),
                    "utils/metrics.py": pulumi.FileAsset(
                        str(
                            Path(__file__).parent.parent
                            / "utils"
                            / "metrics.py"
                        )
                    ),
                    "utils/response.py": pulumi.FileAsset(
                        str(
                            Path(__file__).parent.parent
                            / "utils"
                            / "response.py"
                        )
                    ),
                    "utils/timeout_handler.py": pulumi.FileAsset(
                        str(
                            Path(__file__).parent.parent
                            / "utils"
                            / "timeout_handler.py"
                        )
                    ),
                    "utils/tracing.py": pulumi.FileAsset(
                        str(
                            Path(__file__).parent.parent
                            / "utils"
                            / "tracing.py"
                        )
                    ),
                }
            ),
            handler="stream_processor.lambda_handler",
            role=self.lambda_role.arn,
            timeout=300,  # 5 minutes timeout
            memory_size=256,  # Lightweight processing
            environment={
                "variables": {
                    "LINES_QUEUE_URL": chromadb_queues.lines_queue_url,
                    "WORDS_QUEUE_URL": chromadb_queues.words_queue_url,
                    "LOG_LEVEL": "INFO",
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
            },
            layers=[dynamo_layer.arn],
            opts=ResourceOptions(
                parent=self,
                depends_on=[
                    self.lambda_role,
                    self.stream_log_group,
                ],
            ),
        )

        # Create container-based Lambda for enhanced compaction
        self.enhanced_compaction_function = aws.lambda_.Function(
            f"{name}-enhanced-compaction",
            package_type="Image",
            image_uri=self.docker_image.image_uri,
            role=self.lambda_role.arn,
            timeout=900,  # 15 minutes for compaction operations
            memory_size=1024,  # More memory for ChromaDB operations
            architectures=["arm64"],
            environment={
                "variables": {
                    "DYNAMODB_TABLE_NAME": Output.all(
                        dynamodb_table_arn
                    ).apply(lambda args: args[0].split("/")[-1]),
                    "CHROMADB_BUCKET": chromadb_buckets.bucket_name,
                    "LINES_QUEUE_URL": chromadb_queues.lines_queue_url,
                    "WORDS_QUEUE_URL": chromadb_queues.words_queue_url,
                    "HEARTBEAT_INTERVAL_SECONDS": "60",
                    "LOCK_DURATION_MINUTES": "15",
                    "LOG_LEVEL": "INFO",
                }
            },
            description=(
                "Enhanced ChromaDB compaction handler for stream and "
                "delta message processing"
            ),
            tags={
                "Project": "ChromaDB",
                "Component": "EnhancedCompaction",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(
                parent=self,
                depends_on=[
                    self.lambda_role,
                    self.docker_image,
                    self.compaction_log_group,
                ],
            ),
        )

        # Create event source mappings
        self._create_event_source_mappings(
            name, dynamodb_stream_arn, chromadb_queues
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
            batch_size=100,
            maximum_batching_window_in_seconds=5,
            parallelization_factor=1,
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
            batch_size=10,
            maximum_batching_window_in_seconds=30,
            opts=ResourceOptions(parent=self),
        )

        self.words_event_source_mapping = aws.lambda_.EventSourceMapping(
            f"{name}-words-event-source-mapping",
            event_source_arn=chromadb_queues.words_queue_arn,
            function_name=self.enhanced_compaction_function.arn,
            batch_size=10,
            maximum_batching_window_in_seconds=30,
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
    base_images=None,
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
        base_images: Base images for container builds
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
        base_images=base_images,
        opts=opts,
    )
