"""
SQS Queues for ChromaDB Compaction Pipeline

This module defines the SQS queue infrastructure for notifying the compactor
about new delta files.
"""

# pylint: disable=too-many-instance-attributes  # Pulumi components need many attributes

import json
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, ResourceOptions, Output


class ChromaDBQueues(ComponentResource):
    """
    ComponentResource that creates SQS queues for ChromaDB delta notifications.

    Creates separate queues for lines and words collections:
    - Lines queue for line embedding updates
    - Words queue for word embedding updates
    - Dead letter queues for failed messages
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

        # Create dead letter queues for each collection
        self.lines_dlq = aws.sqs.Queue(
            f"{name}-lines-dlq",
            name=Output.concat("chromadb-lines-dlq-", stack),
            message_retention_seconds=1209600,  # 14 days
            visibility_timeout_seconds=300,  # 5 minutes
            receive_wait_time_seconds=0,  # Short polling
            tags={
                "Project": "ChromaDB",
                "Component": "Lines-DLQ",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        self.words_dlq = aws.sqs.Queue(
            f"{name}-words-dlq",
            name=Output.concat("chromadb-words-dlq-", stack),
            message_retention_seconds=1209600,  # 14 days
            visibility_timeout_seconds=300,  # 5 minutes
            receive_wait_time_seconds=0,  # Short polling
            tags={
                "Project": "ChromaDB",
                "Component": "Words-DLQ",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Create lines queue with redrive policy
        self.lines_queue = aws.sqs.Queue(
            f"{name}-lines-queue",
            name=Output.concat("chromadb-lines-queue-", stack),
            message_retention_seconds=345600,
            visibility_timeout_seconds=900,
            receive_wait_time_seconds=20,
            redrive_policy=Output.all(self.lines_dlq.arn).apply(
                lambda args: json.dumps(
                    {
                        "deadLetterTargetArn": args[0],
                        "maxReceiveCount": 3,  # Retry 3 times before DLQ
                    }
                )
            ),
            tags={
                "Project": "ChromaDB",
                "Component": "Lines-Queue",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Create words queue with redrive policy
        self.words_queue = aws.sqs.Queue(
            f"{name}-words-queue",
            name=Output.concat("chromadb-words-queue-", stack),
            message_retention_seconds=345600,
            visibility_timeout_seconds=900,
            receive_wait_time_seconds=20,
            redrive_policy=Output.all(self.words_dlq.arn).apply(
                lambda args: json.dumps(
                    {
                        "deadLetterTargetArn": args[0],
                        "maxReceiveCount": 3,  # Retry 3 times before DLQ
                    }
                )
            ),
            tags={
                "Project": "ChromaDB",
                "Component": "Words-Queue",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Create queue policies for Lambda access
        self.lines_queue_policy_document = Output.all(
            self.lines_queue.arn, aws.get_caller_identity().account_id
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

        self.words_queue_policy_document = Output.all(
            self.words_queue.arn, aws.get_caller_identity().account_id
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

        self.lines_queue_policy = aws.sqs.QueuePolicy(
            f"{name}-lines-queue-policy",
            queue_url=self.lines_queue.url,
            policy=self.lines_queue_policy_document,
            opts=ResourceOptions(parent=self),
        )

        self.words_queue_policy = aws.sqs.QueuePolicy(
            f"{name}-words-queue-policy",
            queue_url=self.words_queue.url,
            policy=self.words_queue_policy_document,
            opts=ResourceOptions(parent=self),
        )

        # Export useful properties
        self.lines_queue_url = self.lines_queue.url
        self.lines_queue_arn = self.lines_queue.arn
        self.words_queue_url = self.words_queue.url
        self.words_queue_arn = self.words_queue.arn
        self.lines_dlq_arn = self.lines_dlq.arn
        self.words_dlq_arn = self.words_dlq.arn

        # Register outputs
        self.register_outputs(
            {
                "lines_queue_url": self.lines_queue_url,
                "lines_queue_arn": self.lines_queue_arn,
                "words_queue_url": self.words_queue_url,
                "words_queue_arn": self.words_queue_arn,
                "lines_dlq_arn": self.lines_dlq_arn,
                "words_dlq_arn": self.words_dlq_arn,
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
