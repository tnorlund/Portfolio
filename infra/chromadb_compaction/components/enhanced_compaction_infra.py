"""
Infrastructure for Enhanced ChromaDB Compaction Handler Lambda

Creates a Lambda function that processes both stream messages from the DynamoDB
stream processor and traditional delta messages for ChromaDB metadata updates.
"""

# pylint: disable=too-many-instance-attributes,too-many-arguments,too-many-positional-arguments
# Pulumi components naturally have many attributes and parameters

import json
from pathlib import Path
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Output, ResourceOptions

from .sqs_queues import ChromaDBQueues
from .s3_buckets import ChromaDBBuckets


class EnhancedCompactionLambda(ComponentResource):
    """
    ComponentResource for the Enhanced ChromaDB Compaction Lambda.

    Creates:
    - Lambda function for processing stream and delta messages
    - IAM role and policies for DynamoDB, S3, and SQS access
    - CloudWatch log group for monitoring
    - SQS event source mapping for queue processing
    """

    def __init__(
        self,
        name: str,
        chromadb_queues: ChromaDBQueues,
        chromadb_buckets: ChromaDBBuckets,
        dynamodb_table_arn: str,
        stack: Optional[str] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        """
        Initialize the Enhanced Compaction Lambda.

        Args:
            name: The unique name of the resource
            chromadb_queues: The ChromaDB SQS queues component
            chromadb_buckets: The ChromaDB S3 buckets component
            dynamodb_table_arn: ARN of the DynamoDB table
            stack: The Pulumi stack name (defaults to current stack)
            opts: Optional resource options
        """
        super().__init__(
            "chromadb:compaction:EnhancedLambda", name, None, opts
        )

        # Get stack
        if stack is None:
            stack = pulumi.get_stack()

        # Create CloudWatch log group
        self.log_group = aws.cloudwatch.LogGroup(
            f"{name}-log-group",
            name=f"/aws/lambda/chromadb-enhanced-compaction-{stack}",
            retention_in_days=14,  # Keep logs for 2 weeks
            tags={
                "Project": "ChromaDB",
                "Component": "EnhancedCompaction",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Create IAM role for Lambda
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
                "Component": "EnhancedCompaction",
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

        # Create policy for DynamoDB access (for mutex locks)
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
                                    "dynamodb:GetItem",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:DeleteItem",
                                    "dynamodb:Query",
                                    "dynamodb:Scan",
                                ],
                                "Resource": [
                                    args[0],  # Table ARN
                                    f"{args[0]}/index/*",  # GSI ARNs
                                ],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Create policy for S3 access (for ChromaDB snapshots)
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

        # Create policy for SQS access (both lines and words queues)
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
                                    "sqs:ReceiveMessage",
                                    "sqs:DeleteMessage",
                                    "sqs:GetQueueAttributes",
                                    "sqs:SendMessage",
                                    "sqs:SendMessageBatch",
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

        # Create the Lambda function
        self.function = aws.lambda_.Function(
            f"{name}-function",
            name=f"chromadb-enhanced-compaction-{stack}",
            runtime="python3.12",
            code=pulumi.AssetArchive(
                {
                    "enhanced_compaction_handler.py": pulumi.FileAsset(
                        str(
                            Path(__file__).parent.parent
                            / "lambdas"
                            / "enhanced_compaction_handler.py"
                        )
                    ),
                }
            ),
            handler="enhanced_compaction_handler.handle",
            role=self.lambda_role.arn,
            timeout=900,  # 15 minutes for compaction operations
            memory_size=512,  # More memory for ChromaDB operations
            environment={
                "variables": {
                    "DYNAMODB_TABLE_NAME": dynamodb_table_arn.apply(
                        lambda arn: arn.split("/")[-1]
                    ),
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
                    self.dynamodb_policy,
                    self.s3_policy,
                    self.sqs_policy,
                    self.log_group,
                ],
            ),
        )

        # Create SQS event source mappings for both queues
        self.lines_event_source_mapping = aws.lambda_.EventSourceMapping(
            f"{name}-lines-event-source-mapping",
            event_source_arn=chromadb_queues.lines_queue_arn,
            function_name=self.function.arn,
            batch_size=10,
            maximum_batching_window_in_seconds=30,
            maximum_retry_attempts=3,
            maximum_record_age_in_seconds=3600,
            opts=ResourceOptions(parent=self),
        )

        self.words_event_source_mapping = aws.lambda_.EventSourceMapping(
            f"{name}-words-event-source-mapping",
            event_source_arn=chromadb_queues.words_queue_arn,
            function_name=self.function.arn,
            batch_size=10,
            maximum_batching_window_in_seconds=30,
            maximum_retry_attempts=3,
            maximum_record_age_in_seconds=3600,
            opts=ResourceOptions(parent=self),
        )

        # Export useful properties
        self.function_arn = self.function.arn
        self.function_name = self.function.name
        self.role_arn = self.lambda_role.arn

        # Register outputs
        self.register_outputs(
            {
                "function_arn": self.function_arn,
                "function_name": self.function_name,
                "role_arn": self.role_arn,
            }
        )


def create_enhanced_compaction_lambda(
    name: str = "chromadb-enhanced-compaction",
    chromadb_queues: ChromaDBQueues = None,
    chromadb_buckets: ChromaDBBuckets = None,
    dynamodb_table_arn: str = None,
    opts: Optional[ResourceOptions] = None,
) -> EnhancedCompactionLambda:
    """
    Factory function to create the Enhanced ChromaDB Compaction Lambda.

    Args:
        name: Base name for the resources
        chromadb_queues: The ChromaDB SQS queues component
        chromadb_buckets: The ChromaDB S3 buckets component
        dynamodb_table_arn: ARN of the DynamoDB table
        opts: Optional resource options

    Returns:
        EnhancedCompactionLambda component
    """
    if not chromadb_queues:
        raise ValueError("chromadb_queues parameter is required")
    if not chromadb_buckets:
        raise ValueError("chromadb_buckets parameter is required")
    if not dynamodb_table_arn:
        raise ValueError("dynamodb_table_arn parameter is required")

    return EnhancedCompactionLambda(
        name=name,
        chromadb_queues=chromadb_queues,
        chromadb_buckets=chromadb_buckets,
        dynamodb_table_arn=dynamodb_table_arn,
        opts=opts,
    )
