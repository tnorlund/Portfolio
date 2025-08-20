"""
Infrastructure configuration for DynamoDB Stream Processor Lambda

Creates the Lambda function that processes DynamoDB stream events for ChromaDB
metadata synchronization. Integrates with existing SQS queue infrastructure.
"""

# pylint: disable=too-many-instance-attributes,too-many-arguments,too-many-positional-arguments,duplicate-code
# Pulumi components naturally have many attributes and parameters,
# some duplication with enhanced_compaction_infra is expected

import json
from pathlib import Path
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Output, ResourceOptions

from .sqs_queues import ChromaDBQueues


class StreamProcessorLambda(ComponentResource):
    """
    ComponentResource for the DynamoDB Stream Processor Lambda.

    Creates:
    - Lambda function for processing stream events
    - IAM role and policies for DynamoDB and SQS access
    - CloudWatch log group for monitoring
    """

    def __init__(
        self,
        name: str,
        chromadb_queues: ChromaDBQueues,
        dynamodb_table_arn: str,
        stack: Optional[str] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        """
        Initialize the Stream Processor Lambda.

        Args:
            name: The unique name of the resource
            chromadb_queues: The existing ChromaDB SQS queues component
            dynamodb_table_arn: ARN of the DynamoDB table to stream from
            stack: The Pulumi stack name (defaults to current stack)
            opts: Optional resource options
        """
        super().__init__("chromadb:stream:ProcessorLambda", name, None, opts)

        # Get stack
        if stack is None:
            stack = pulumi.get_stack()

        # Create CloudWatch log group
        self.log_group = aws.cloudwatch.LogGroup(
            f"{name}-log-group",
            name=f"/aws/lambda/chromadb-stream-processor-{stack}",
            retention_in_days=14,  # Keep logs for 2 weeks
            tags={
                "Project": "ChromaDB",
                "Component": "StreamProcessor",
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
                "Component": "StreamProcessor",
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

        # Create policy for DynamoDB Streams access
        self.dynamodb_streams_policy = aws.iam.RolePolicy(
            f"{name}-dynamodb-streams-policy",
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
                                ],
                                "Resource": [
                                    args[0],  # Table ARN
                                    # Stream ARN pattern
                                    f"{args[0]}/stream/*",
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Action": ["dynamodb:ListStreams"],
                                "Resource": "*",
                            },
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
                                    "sqs:SendMessage",
                                    "sqs:SendMessageBatch",
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

        # Create the Lambda function
        self.function = aws.lambda_.Function(
            f"{name}-function",
            name=f"chromadb-stream-processor-{stack}",
            runtime="python3.12",
            code=pulumi.AssetArchive(
                {
                    "stream_processor.py": pulumi.FileAsset(
                        str(
                            Path(__file__).parent.parent
                            / "lambdas"
                            / "stream_processor.py"
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
            opts=ResourceOptions(
                parent=self,
                depends_on=[
                    self.lambda_role,
                    self.dynamodb_streams_policy,
                    self.sqs_policy,
                    self.log_group,
                ],
            ),
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


class DynamoDBStreamEventSourceMapping(ComponentResource):
    """
    ComponentResource for connecting DynamoDB Stream to Lambda.

    Creates the event source mapping that triggers the Lambda function
    when DynamoDB stream events occur.
    """

    def __init__(
        self,
        name: str,
        lambda_function: aws.lambda_.Function,
        dynamodb_stream_arn: str,
        opts: Optional[ResourceOptions] = None,
    ):
        """
        Initialize the DynamoDB Stream Event Source Mapping.

        Args:
            name: The unique name of the resource
            lambda_function: The Lambda function to trigger
            dynamodb_stream_arn: ARN of the DynamoDB stream
            opts: Optional resource options
        """
        super().__init__(
            "chromadb:stream:EventSourceMapping", name, None, opts
        )

        # Create event source mapping
        self.event_source_mapping = aws.lambda_.EventSourceMapping(
            f"{name}-event-source-mapping",
            event_source_arn=dynamodb_stream_arn,
            function_name=lambda_function.arn,
            starting_position="LATEST",
            batch_size=100,
            maximum_batching_window_in_seconds=5,
            parallelization_factor=1,
            maximum_retry_attempts=3,
            maximum_record_age_in_seconds=3600,
            bisect_batch_on_function_error=True,
            opts=ResourceOptions(parent=self),
        )

        # Export useful properties
        self.mapping_uuid = self.event_source_mapping.uuid
        self.state = self.event_source_mapping.state

        # Register outputs
        self.register_outputs(
            {
                "mapping_uuid": self.mapping_uuid,
                "state": self.state,
            }
        )


def create_stream_processor(
    name: str = "chromadb-stream-processor",
    chromadb_queues: ChromaDBQueues = None,
    dynamodb_table_arn: str = None,
    dynamodb_stream_arn: str = None,
    opts: Optional[ResourceOptions] = None,
) -> tuple[StreamProcessorLambda, DynamoDBStreamEventSourceMapping]:
    """
    Factory function to create the complete stream processing setup.

    Args:
        name: Base name for the resources
        chromadb_queues: The existing ChromaDB SQS queues component
        dynamodb_table_arn: ARN of the DynamoDB table
        dynamodb_stream_arn: ARN of the DynamoDB stream
        opts: Optional resource options

    Returns:
        Tuple of (StreamProcessorLambda, DynamoDBStreamEventSourceMapping)
    """
    if not chromadb_queues:
        raise ValueError("chromadb_queues parameter is required")
    if not dynamodb_table_arn:
        raise ValueError("dynamodb_table_arn parameter is required")
    if not dynamodb_stream_arn:
        raise ValueError("dynamodb_stream_arn parameter is required")

    # Create the Lambda function
    lambda_processor = StreamProcessorLambda(
        name=f"{name}-lambda",
        chromadb_queues=chromadb_queues,
        dynamodb_table_arn=dynamodb_table_arn,
        opts=opts,
    )

    # Create the event source mapping
    event_mapping = DynamoDBStreamEventSourceMapping(
        name=f"{name}-mapping",
        lambda_function=lambda_processor.function,
        dynamodb_stream_arn=dynamodb_stream_arn,
        opts=(
            ResourceOptions.merge(
                ResourceOptions(parent=lambda_processor), opts
            )
            if opts
            else ResourceOptions(parent=lambda_processor)
        ),
    )

    return lambda_processor, event_mapping
