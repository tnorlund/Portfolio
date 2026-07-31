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
from pulumi import ComponentResource, Config, Output, ResourceOptions

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

        self.docker_image = DockerImageComponent(
            f"{name}-docker",
            lambda_config={
                "role_arn": self.lambda_role.arn,
                # Lambda processes up to 1000 messages per batch (Standard queue).
                # 15 min allows draining large backlogs in one invocation.
                # Must match SQS visibility timeout.
                "timeout": 900,  # 15 minutes - must match visibility timeout
                # Increased memory to 10240MB (10GB, Lambda max) due to OOM errors:
                # - ChromaDB collection with ~70K embeddings uses ~8GB
                # - Snapshot validation downloads and loads full collection
                # - Standard queue batching processes up to 1000 messages per invocation
                "memory_size": 10240,  # 10GB (Lambda max) for large collections
                # Increased ephemeral storage from 5GB to 10GB for large snapshot operations
                "ephemeral_storage": 10240,  # 10GB for ChromaDB snapshots (largest seen: 552MB)
                # Must equal the sum of the two event source mappings'
                # maximum_concurrency (2 lines + 2 words).  Standard-queue ESMs
                # cannot go below 2, so reserved=2 left half the poller slots
                # permanently throttled — and a throttled poll still increments
                # ApproximateReceiveCount, burning the message's retention
                # budget until it expired unprocessed (~27k throttles/day
                # during the 07-12 storm).
                #
                # An invocation that loses the per-collection lock is cheap,
                # not wasteful: acquire() fails immediately and the handler
                # returns the batch for retry rather than blocking on the lock.
                # Residual lock contention is now visible via the
                # CompactionLockAcquisitionFailed alarm instead of hiding in
                # SQS receive counts.
                "reserved_concurrent_executions": 4,
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
                    "DYNAMODB_TABLE_NAME": Output.all(
                        dynamodb_table_arn
                    ).apply(lambda args: args[0].split("/")[-1]),
                    "CHROMADB_BUCKET": chromadb_buckets.bucket_name,
                    "LINES_QUEUE_URL": chromadb_queues.lines_queue_url,
                    "WORDS_QUEUE_URL": chromadb_queues.words_queue_url,
                    "HEARTBEAT_INTERVAL_SECONDS": "30",
                    "LOCK_DURATION_MINUTES": "3",
                    "MAX_HEARTBEAT_FAILURES": "3",
                    "LOG_LEVEL": "INFO",
                    # Enable custom CloudWatch metrics now that Lambda has internet
                    # access via NAT instance. If timeouts occur, consider adding a
                    # CloudWatch Metrics Interface VPC Endpoint (~$7/month).
                    "ENABLE_METRICS": "true",
                    # Max messages to process per compaction cycle
                    # Standard queues allow batch_size=1000 from Lambda event source
                    # Lambda sorts messages (REMOVE first) and deduplicates within batch
                    "MAX_MESSAGES_PER_COMPACTION": "1000",
                    # Chroma Cloud dual-write configuration
                    # Feature flag: "true" enables dual-write to both S3 and Chroma Cloud
                    # Set via: pulumi config set portfolio:CHROMA_CLOUD_ENABLED true
                    "CHROMA_CLOUD_ENABLED": Config("portfolio").get(
                        "CHROMA_CLOUD_ENABLED"
                    )
                    or "false",
                    # Chroma Cloud API key (stored as secret in Pulumi config)
                    # Set via: pulumi config set --secret portfolio:CHROMA_CLOUD_API_KEY xxx
                    "CHROMA_CLOUD_API_KEY": Config("portfolio").get_secret(
                        "CHROMA_CLOUD_API_KEY"
                    )
                    or "",
                    # Chroma Cloud tenant ID from dashboard
                    # Set via: pulumi config set portfolio:CHROMA_CLOUD_TENANT xxx
                    "CHROMA_CLOUD_TENANT": Config("portfolio").get(
                        "CHROMA_CLOUD_TENANT"
                    )
                    or "",
                    # Chroma Cloud database name (defaults to "default")
                    # Set via: pulumi config set portfolio:CHROMA_CLOUD_DATABASE xxx
                    "CHROMA_CLOUD_DATABASE": Config("portfolio").get(
                        "CHROMA_CLOUD_DATABASE"
                    )
                    or "default",
                },
                "vpc_config": {
                    "subnet_ids": vpc_subnet_ids,
                    "security_group_ids": [lambda_security_group_id],
                },
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
                    "utils/sqs_batching.py": pulumi.FileAsset(
                        str(
                            Path(__file__).parent.parent
                            / "lambdas"
                            / "utils"
                            / "sqs_batching.py"
                        )
                    ),
                    "utils/lambda_types.py": pulumi.FileAsset(
                        str(
                            Path(__file__).parent.parent
                            / "lambdas"
                            / "utils"
                            / "lambda_types.py"
                        )
                    ),
                }
            ),
            handler="stream_processor.lambda_handler",
            role=self.lambda_role.arn,
            timeout=120,  # 2 minutes timeout (reduced from 5 to prevent long hangs)
            memory_size=256,  # Lightweight processing
            # Removed reserved_concurrent_executions to allow parallel processing
            # Stream processor can safely process multiple images in parallel
            # No VPC config - stream processor only needs AWS service access (DynamoDB, SQS, CloudWatch)
            environment={
                "variables": {
                    "LINES_QUEUE_URL": chromadb_queues.lines_queue_url,
                    "WORDS_QUEUE_URL": chromadb_queues.words_queue_url,
                    "RECEIPT_SUMMARY_QUEUE_URL": chromadb_queues.summary_queue_url,
                    "LINE_ITEM_QUEUE_URL": chromadb_queues.line_item_queue_url,
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

        # Use the Lambda function created by DockerImageComponent
        self.enhanced_compaction_function = (
            self.docker_image.docker_image.lambda_function
        )

        # Create event source mappings
        self._create_event_source_mappings(
            name, dynamodb_stream_arn, chromadb_queues
        )

        # Receipt Summary Updater Lambda (zip-based)
        # Processes SQS messages to recompute ReceiptSummary when labels change
        summary_updater_code = pulumi.AssetArchive(
            {
                "handler.py": pulumi.FileAsset(
                    str(
                        Path(__file__).parent.parent.parent
                        / "receipt_summary_updater"
                        / "handler.py"
                    )
                ),
                "summary_processor.py": pulumi.FileAsset(
                    str(
                        Path(__file__).parent.parent.parent
                        / "receipt_summary_updater"
                        / "summary_processor.py"
                    )
                ),
            }
        )

        self.summary_updater_log_group = aws.cloudwatch.LogGroup(
            f"{name}-summary-updater-log-group",
            retention_in_days=14,
            tags={
                "Project": "ChromaDB",
                "Component": "SummaryUpdater",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        self.summary_updater_function = aws.lambda_.Function(
            f"{name}-summary-updater",
            runtime="python3.12",
            architectures=["arm64"],
            code=summary_updater_code,
            handler="handler.lambda_handler",
            role=self.lambda_role.arn,
            timeout=60,  # 60s; SQS visibility is 2x (120s) for retry headroom
            memory_size=256,  # Lightweight processing
            environment={
                "variables": {
                    "DYNAMODB_TABLE_NAME": Output.all(
                        dynamodb_table_arn
                    ).apply(lambda args: args[0].split("/")[-1]),
                    "LOG_LEVEL": "INFO",
                }
            },
            description=(
                "Recomputes ReceiptSummary when ReceiptWordLabel or "
                "ReceiptPlace records change"
            ),
            tags={
                "Project": "ChromaDB",
                "Component": "SummaryUpdater",
                "Environment": stack,
                "ManagedBy": "Pulumi",
                "environment": stack,
            },
            layers=[dynamo_layer.arn],
            opts=ResourceOptions(
                parent=self,
                depends_on=[
                    self.lambda_role,
                    self.summary_updater_log_group,
                ],
                ignore_changes=["layers"],
            ),
        )

        # Event source mapping for summary queue
        self.summary_event_source_mapping = aws.lambda_.EventSourceMapping(
            f"{name}-summary-event-source-mapping",
            event_source_arn=chromadb_queues.summary_queue_arn,
            function_name=self.summary_updater_function.arn,
            batch_size=100,  # Process up to 100 messages per invocation
            maximum_batching_window_in_seconds=5,  # Batch for up to 5 seconds
            function_response_types=["ReportBatchItemFailures"],
            opts=ResourceOptions(parent=self),
        )

        # ------------------------------------------------------------------
        # Line-item updater: rewrites RECEIPT_LINE_ITEM rows when a
        # receipt's summary changes. Bundles the CANONICAL extraction
        # modules from receipt_upload as FileAssets -- no copies live in
        # the repo, so the logic cannot fork (the build_receipt_rows
        # shadowing lesson).
        # ------------------------------------------------------------------
        _repo_root = Path(__file__).parent.parent.parent.parent
        line_item_updater_code = pulumi.AssetArchive(
            {
                "handler.py": pulumi.FileAsset(
                    _repo_root
                    / "infra"
                    / "receipt_line_item_updater"
                    / "handler.py"
                ),
                "line_item_processor.py": pulumi.FileAsset(
                    _repo_root
                    / "infra"
                    / "receipt_line_item_updater"
                    / "line_item_processor.py"
                ),
                "receipt_upload/__init__.py": pulumi.StringAsset(
                    "# deploy-time stub: only line_items is bundled; the real\n"
                    "# receipt_upload __init__ pulls PIL and the full package.\n"
                ),
                # Must be non-empty: an empty StringAsset serializes with no
                # "text" field and crashes the Python SDK when the engine
                # echoes the archive back ("Invalid asset encountered when
                # unmarshalling resource property").
                "receipt_upload/line_items/__init__.py": pulumi.StringAsset(
                    "# namespace stub\n"
                ),
                "receipt_upload/line_items/geometry.py": pulumi.FileAsset(
                    _repo_root
                    / "receipt_upload"
                    / "receipt_upload"
                    / "line_items"
                    / "geometry.py"
                ),
                "receipt_upload/line_items/blocks.py": pulumi.FileAsset(
                    _repo_root
                    / "receipt_upload"
                    / "receipt_upload"
                    / "line_items"
                    / "blocks.py"
                ),
                "receipt_upload/line_items/assets/block_role_priors_v1.json": pulumi.FileAsset(
                    _repo_root
                    / "receipt_upload"
                    / "receipt_upload"
                    / "line_items"
                    / "assets"
                    / "block_role_priors_v1.json"
                ),
                "receipt_upload/line_items/assets/block_role_priors_v2.json": pulumi.FileAsset(
                    _repo_root
                    / "receipt_upload"
                    / "receipt_upload"
                    / "line_items"
                    / "assets"
                    / "block_role_priors_v2.json"
                ),
            }
        )

        self.line_item_updater_log_group = aws.cloudwatch.LogGroup(
            f"{name}-line-item-updater-log-group",
            retention_in_days=14,
            tags={
                "Project": "ChromaDB",
                "Component": "LineItemUpdater",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        self.line_item_updater_function = aws.lambda_.Function(
            f"{name}-line-item-updater",
            runtime="python3.12",
            architectures=["arm64"],
            code=line_item_updater_code,
            handler="handler.lambda_handler",
            role=self.lambda_role.arn,
            timeout=120,  # extraction fetches words+sections; queue vis is 2x
            memory_size=512,
            environment={
                "variables": {
                    "DYNAMODB_TABLE_NAME": Output.all(
                        dynamodb_table_arn
                    ).apply(lambda args: args[0].split("/")[-1]),
                    "LOG_LEVEL": "INFO",
                }
            },
            description=(
                "Rewrites RECEIPT_LINE_ITEM rows via the band-block decoder "
                "when a receipt's summary changes"
            ),
            tags={
                "Project": "ChromaDB",
                "Component": "LineItemUpdater",
                "Environment": stack,
                "ManagedBy": "Pulumi",
                "environment": stack,
            },
            layers=[dynamo_layer.arn],
            opts=ResourceOptions(
                parent=self,
                depends_on=[
                    self.lambda_role,
                    self.line_item_updater_log_group,
                ],
                ignore_changes=["layers"],
            ),
        )

        self.line_item_event_source_mapping = aws.lambda_.EventSourceMapping(
            f"{name}-line-item-event-source-mapping",
            event_source_arn=chromadb_queues.line_item_queue_arn,
            function_name=self.line_item_updater_function.arn,
            batch_size=50,
            maximum_batching_window_in_seconds=5,
            function_response_types=["ReportBatchItemFailures"],
            # The inline sqs_policy grants receive/delete on the queue;
            # without the explicit dependency AWS can reject the mapping
            # when it is created before the IAM update lands.
            opts=ResourceOptions(
                parent=self, depends_on=[self.sqs_policy]
            ),
        )

        # Export useful properties
        self.stream_processor_arn = self.stream_processor_function.arn
        self.enhanced_compaction_arn = self.enhanced_compaction_function.arn
        self.summary_updater_arn = self.summary_updater_function.arn
        self.line_item_updater_arn = self.line_item_updater_function.arn
        self.role_arn = self.lambda_role.arn

        # Register outputs
        self.register_outputs(
            {
                "stream_processor_arn": self.stream_processor_arn,
                "enhanced_compaction_arn": self.enhanced_compaction_arn,
                "summary_updater_arn": self.summary_updater_arn,
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
                                    # line-item updater writes rows via
                                    # batch_write_item
                                    "dynamodb:BatchWriteItem",
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
                chromadb_queues.summary_queue_arn,
                chromadb_queues.summary_dlq_arn,
                chromadb_queues.line_item_queue_arn,
                chromadb_queues.line_item_dlq_arn,
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
                                    args[4],  # Summary queue ARN
                                    args[5],  # Summary DLQ ARN
                                    args[6],  # Line-item queue ARN
                                    args[7],  # Line-item DLQ ARN
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
        # Standard queues support batch_size up to 10,000 and batching windows.
        # Lambda sorts messages (REMOVE first) and uses within-batch deduplication
        # to prevent orphaned embeddings.
        self.lines_event_source_mapping = aws.lambda_.EventSourceMapping(
            f"{name}-lines-event-source-mapping",
            event_source_arn=chromadb_queues.lines_queue_arn,
            function_name=self.enhanced_compaction_function.arn,
            batch_size=1000,  # Standard queues support up to 10,000
            maximum_batching_window_in_seconds=5,  # Batch for up to 5 seconds
            function_response_types=["ReportBatchItemFailures"],
            # Minimum for standard queues is 2.  Combined with the
            # Lambda's reserved_concurrent_executions=2, each queue
            # effectively gets one slot while both run in parallel.
            scaling_config=aws.lambda_.EventSourceMappingScalingConfigArgs(
                maximum_concurrency=2,
            ),
            opts=ResourceOptions(parent=self),
        )

        self.words_event_source_mapping = aws.lambda_.EventSourceMapping(
            f"{name}-words-event-source-mapping",
            event_source_arn=chromadb_queues.words_queue_arn,
            function_name=self.enhanced_compaction_function.arn,
            batch_size=1000,  # Standard queues support up to 10,000
            maximum_batching_window_in_seconds=5,  # Batch for up to 5 seconds
            function_response_types=["ReportBatchItemFailures"],
            scaling_config=aws.lambda_.EventSourceMappingScalingConfigArgs(
                maximum_concurrency=2,
            ),
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
        opts=opts,
    )
