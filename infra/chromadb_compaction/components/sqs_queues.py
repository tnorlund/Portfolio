"""
SQS Queues for ChromaDB Compaction Pipeline

This module defines the SQS queue infrastructure for notifying the compactor
about new delta files.

Architecture:
- Standard queues for INSERT/MODIFY operations (high throughput, batch up to 1000)
- FIFO queues for REMOVE operations only (ordered deletes, batch 10)
"""

# pylint: disable=too-many-instance-attributes  # Pulumi components need many attributes

import json
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Output, ResourceOptions


def _create_queue_policy_document(
    queue_arn: Output[str], account_id: str
) -> Output[str]:
    """Create a queue policy document for Lambda access."""
    return Output.all(queue_arn, account_id).apply(
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
                        "Principal": {"AWS": f"arn:aws:iam::{args[1]}:root"},
                        "Action": "sqs:*",
                        "Resource": args[0],
                    },
                ],
            }
        )
    )


class ChromaDBQueues(ComponentResource):
    """
    ComponentResource that creates SQS queues for ChromaDB delta notifications.

    Creates a hybrid queue architecture:
    - Standard queues for INSERT/MODIFY (high throughput)
    - FIFO queues for REMOVE only (ordered deletes)
    - Dead letter queues for failed messages
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

        if stack is None:
            stack = pulumi.get_stack()

        account_id = aws.get_caller_identity().account_id

        # =========================================================================
        # Standard Queues (INSERT/MODIFY) - High Throughput
        # =========================================================================

        # Standard DLQs
        self.lines_standard_dlq = aws.sqs.Queue(
            f"{name}-lines-standard-dlq",
            message_retention_seconds=1209600,  # 14 days
            visibility_timeout_seconds=300,
            receive_wait_time_seconds=0,
            tags={
                "Project": "ChromaDB",
                "Component": "Lines-Standard-DLQ",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        self.words_standard_dlq = aws.sqs.Queue(
            f"{name}-words-standard-dlq",
            message_retention_seconds=1209600,  # 14 days
            visibility_timeout_seconds=300,
            receive_wait_time_seconds=0,
            tags={
                "Project": "ChromaDB",
                "Component": "Words-Standard-DLQ",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Standard queues for INSERT/MODIFY operations
        self.lines_standard_queue = aws.sqs.Queue(
            f"{name}-lines-standard-queue",
            message_retention_seconds=345600,  # 4 days
            visibility_timeout_seconds=120,
            receive_wait_time_seconds=20,  # Long polling
            redrive_policy=Output.all(self.lines_standard_dlq.arn).apply(
                lambda args: json.dumps(
                    {"deadLetterTargetArn": args[0], "maxReceiveCount": 3}
                )
            ),
            tags={
                "Project": "ChromaDB",
                "Component": "Lines-Standard-Queue",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        self.words_standard_queue = aws.sqs.Queue(
            f"{name}-words-standard-queue",
            message_retention_seconds=345600,  # 4 days
            visibility_timeout_seconds=120,
            receive_wait_time_seconds=20,  # Long polling
            redrive_policy=Output.all(self.words_standard_dlq.arn).apply(
                lambda args: json.dumps(
                    {"deadLetterTargetArn": args[0], "maxReceiveCount": 3}
                )
            ),
            tags={
                "Project": "ChromaDB",
                "Component": "Words-Standard-Queue",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # =========================================================================
        # FIFO Queues (REMOVE only) - Ordered Deletes
        # =========================================================================

        # FIFO DLQs for delete operations
        self.lines_delete_dlq = aws.sqs.Queue(
            f"{name}-lines-delete-dlq",
            fifo_queue=True,
            content_based_deduplication=True,
            message_retention_seconds=1209600,  # 14 days
            visibility_timeout_seconds=300,
            receive_wait_time_seconds=0,
            tags={
                "Project": "ChromaDB",
                "Component": "Lines-Delete-DLQ",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        self.words_delete_dlq = aws.sqs.Queue(
            f"{name}-words-delete-dlq",
            fifo_queue=True,
            content_based_deduplication=True,
            message_retention_seconds=1209600,  # 14 days
            visibility_timeout_seconds=300,
            receive_wait_time_seconds=0,
            tags={
                "Project": "ChromaDB",
                "Component": "Words-Delete-DLQ",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # FIFO queues for REMOVE operations only
        self.lines_delete_queue = aws.sqs.Queue(
            f"{name}-lines-delete-queue",
            fifo_queue=True,
            content_based_deduplication=True,
            message_retention_seconds=345600,  # 4 days
            visibility_timeout_seconds=120,
            receive_wait_time_seconds=20,
            redrive_policy=Output.all(self.lines_delete_dlq.arn).apply(
                lambda args: json.dumps(
                    {"deadLetterTargetArn": args[0], "maxReceiveCount": 3}
                )
            ),
            tags={
                "Project": "ChromaDB",
                "Component": "Lines-Delete-Queue",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        self.words_delete_queue = aws.sqs.Queue(
            f"{name}-words-delete-queue",
            fifo_queue=True,
            content_based_deduplication=True,
            message_retention_seconds=345600,  # 4 days
            visibility_timeout_seconds=120,
            receive_wait_time_seconds=20,
            redrive_policy=Output.all(self.words_delete_dlq.arn).apply(
                lambda args: json.dumps(
                    {"deadLetterTargetArn": args[0], "maxReceiveCount": 3}
                )
            ),
            tags={
                "Project": "ChromaDB",
                "Component": "Words-Delete-Queue",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # =========================================================================
        # Queue Policies
        # =========================================================================

        # Standard queue policies
        self.lines_standard_queue_policy = aws.sqs.QueuePolicy(
            f"{name}-lines-standard-queue-policy",
            queue_url=self.lines_standard_queue.url,
            policy=_create_queue_policy_document(
                self.lines_standard_queue.arn, account_id
            ),
            opts=ResourceOptions(parent=self),
        )

        self.words_standard_queue_policy = aws.sqs.QueuePolicy(
            f"{name}-words-standard-queue-policy",
            queue_url=self.words_standard_queue.url,
            policy=_create_queue_policy_document(
                self.words_standard_queue.arn, account_id
            ),
            opts=ResourceOptions(parent=self),
        )

        # FIFO delete queue policies
        self.lines_delete_queue_policy = aws.sqs.QueuePolicy(
            f"{name}-lines-delete-queue-policy",
            queue_url=self.lines_delete_queue.url,
            policy=_create_queue_policy_document(
                self.lines_delete_queue.arn, account_id
            ),
            opts=ResourceOptions(parent=self),
        )

        self.words_delete_queue_policy = aws.sqs.QueuePolicy(
            f"{name}-words-delete-queue-policy",
            queue_url=self.words_delete_queue.url,
            policy=_create_queue_policy_document(
                self.words_delete_queue.arn, account_id
            ),
            opts=ResourceOptions(parent=self),
        )

        # =========================================================================
        # Export Properties
        # =========================================================================

        # Standard queue exports (INSERT/MODIFY)
        self.lines_standard_queue_url = self.lines_standard_queue.url
        self.lines_standard_queue_arn = self.lines_standard_queue.arn
        self.words_standard_queue_url = self.words_standard_queue.url
        self.words_standard_queue_arn = self.words_standard_queue.arn

        # FIFO delete queue exports (REMOVE)
        self.lines_delete_queue_url = self.lines_delete_queue.url
        self.lines_delete_queue_arn = self.lines_delete_queue.arn
        self.words_delete_queue_url = self.words_delete_queue.url
        self.words_delete_queue_arn = self.words_delete_queue.arn

        # DLQ exports
        self.lines_standard_dlq_arn = self.lines_standard_dlq.arn
        self.words_standard_dlq_arn = self.words_standard_dlq.arn
        self.lines_delete_dlq_arn = self.lines_delete_dlq.arn
        self.words_delete_dlq_arn = self.words_delete_dlq.arn

        # Register outputs
        self.register_outputs(
            {
                # Standard queues
                "lines_standard_queue_url": self.lines_standard_queue_url,
                "lines_standard_queue_arn": self.lines_standard_queue_arn,
                "words_standard_queue_url": self.words_standard_queue_url,
                "words_standard_queue_arn": self.words_standard_queue_arn,
                # FIFO delete queues
                "lines_delete_queue_url": self.lines_delete_queue_url,
                "lines_delete_queue_arn": self.lines_delete_queue_arn,
                "words_delete_queue_url": self.words_delete_queue_url,
                "words_delete_queue_arn": self.words_delete_queue_arn,
                # DLQs
                "lines_standard_dlq_arn": self.lines_standard_dlq_arn,
                "words_standard_dlq_arn": self.words_standard_dlq_arn,
                "lines_delete_dlq_arn": self.lines_delete_dlq_arn,
                "words_delete_dlq_arn": self.words_delete_dlq_arn,
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
