"""
SQS Queues for ChromaDB Compaction Pipeline

This module defines the SQS queue infrastructure for notifying the compactor
about new delta files.
"""

import json
import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, ResourceOptions, Output
from typing import Optional


class ChromaDBQueues(ComponentResource):
    """
    ComponentResource that creates SQS queues for ChromaDB delta notifications.

    Creates:
    - Main queue for delta notifications
    - Dead letter queue for failed messages
    - Proper visibility timeout for Lambda processing
    """

    def __init__(
        self,
        name: str,
        stack: Optional[str] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        """
        Initialize ChromaDB SQS queues.

        Args:
            name: The unique name of the resource
            stack: The Pulumi stack name (defaults to current stack)
            opts: Optional resource options
        """
        super().__init__("chromadb:queues:SQSQueues", name, None, opts)

        # Get stack
        if stack is None:
            stack = pulumi.get_stack()

        # Create dead letter queue first
        self.dlq = aws.sqs.Queue(
            f"{name}-delta-dlq",
            name=Output.concat("chromadb-delta-dlq-", stack),
            message_retention_seconds=1209600,  # 14 days
            visibility_timeout_seconds=300,  # 5 minutes
            receive_wait_time_seconds=0,  # Short polling
            tags={
                "Project": "ChromaDB",
                "Component": "DLQ",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Create main queue with redrive policy
        self.delta_queue = aws.sqs.Queue(
            f"{name}-delta-queue",
            name=Output.concat("chromadb-delta-queue-", stack),
            message_retention_seconds=345600,  # 4 days
            visibility_timeout_seconds=900,  # 15 minutes (match compaction timeout)
            receive_wait_time_seconds=20,  # Long polling for efficiency
            redrive_policy=Output.all(self.dlq.arn).apply(
                lambda args: json.dumps(
                    {
                        "deadLetterTargetArn": args[0],
                        "maxReceiveCount": 3,  # Retry 3 times before DLQ
                    }
                )
            ),
            tags={
                "Project": "ChromaDB",
                "Component": "Queue",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Create queue policy for Lambda access
        self.queue_policy_document = Output.all(
            self.delta_queue.arn, aws.get_caller_identity().account_id
        ).apply(
            lambda args: json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "AllowLambdaAccess",
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": [
                                "sqs:ReceiveMessage",
                                "sqs:DeleteMessage",
                                "sqs:GetQueueAttributes",
                            ],
                            "Resource": args[0],
                        },
                        {
                            "Sid": "AllowAccountAccess",
                            "Effect": "Allow",
                            "Principal": {
                                "AWS": f"arn:aws:iam::{args[1]}:root"
                            },
                            "Action": "sqs:*",
                            "Resource": args[0],
                        },
                    ],
                }
            )
        )

        self.queue_policy = aws.sqs.QueuePolicy(
            f"{name}-queue-policy",
            queue_url=self.delta_queue.url,
            policy=self.queue_policy_document,
            opts=ResourceOptions(parent=self),
        )

        # Export useful properties
        self.delta_queue_url = self.delta_queue.url
        self.delta_queue_arn = self.delta_queue.arn
        self.dlq_url = self.dlq.url
        self.dlq_arn = self.dlq.arn

        # Register outputs
        self.register_outputs(
            {
                "delta_queue_url": self.delta_queue_url,
                "delta_queue_arn": self.delta_queue_arn,
                "dlq_url": self.dlq_url,
                "dlq_arn": self.dlq_arn,
            }
        )


def create_chromadb_queues(
    name: str = "chromadb",
    opts: Optional[ResourceOptions] = None,
) -> ChromaDBQueues:
    """
    Factory function to create ChromaDB SQS queues.

    Args:
        name: Base name for the resources
        opts: Optional resource options

    Returns:
        ChromaDBQueues component resource
    """
    return ChromaDBQueues(name, opts=opts)
