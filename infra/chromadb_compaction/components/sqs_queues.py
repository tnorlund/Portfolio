"""
SQS Queues for ChromaDB Compaction Pipeline

This module defines the SQS queue infrastructure for notifying the compactor
about new delta files.

Architecture:
- Standard queues for high throughput (batch up to 1000)
- Lambda sorts messages: REMOVE first, then INSERT/MODIFY
- Within-batch deduplication prevents orphaned embeddings
"""

# pylint: disable=too-many-instance-attributes  # Pulumi components need many attributes

import json
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Output, ResourceOptions


def _create_queue_policy_document(
    queue_arn: Output[str],
    producer_role_arns: list[Output[str]],
) -> Output[str]:
    """Create a queue policy document with least-privilege access.

    Args:
        queue_arn: ARN of the SQS queue
        producer_role_arns: List of IAM role ARNs allowed to send messages
            (e.g., stream processor Lambda role). Empty list disables producer access.

    Returns:
        JSON policy document as a Pulumi Output
    """
    # Combine queue ARN with all producer role ARNs for Output.all()
    all_outputs: list[Output[str]] = [queue_arn, *producer_role_arns]

    def build_policy(args: list[str]) -> str:
        resolved_queue_arn = args[0]
        resolved_role_arns = args[1:]

        statements: list[dict] = [
            # Compactor Lambda needs to receive/delete messages
            {
                "Sid": "AllowCompactorLambdaConsume",
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": [
                    "sqs:ReceiveMessage",
                    "sqs:DeleteMessage",
                    "sqs:GetQueueAttributes",
                ],
                "Resource": resolved_queue_arn,
            },
        ]

        # Only add producer statement if role ARNs are provided
        # Least-privilege: only stream processor roles can send messages
        if resolved_role_arns:
            statements.append(
                {
                    "Sid": "AllowStreamProcessorProduce",
                    "Effect": "Allow",
                    "Principal": {"AWS": resolved_role_arns},
                    "Action": [
                        "sqs:SendMessage",
                        "sqs:SendMessageBatch",
                    ],
                    "Resource": resolved_queue_arn,
                }
            )

        return json.dumps({"Version": "2012-10-17", "Statement": statements})

    return Output.all(*all_outputs).apply(build_policy)


class ChromaDBQueues(ComponentResource):
    """
    ComponentResource that creates SQS queues for ChromaDB delta notifications.

    Creates Standard queues for high throughput:
    - Lines queue for line embedding updates
    - Words queue for word embedding updates
    - Dead letter queues for failed messages

    The compactor Lambda handles ordering by sorting messages within each batch
    (REMOVE first) and using within-batch deduplication to prevent orphans.
    """

    def __init__(
        self,
        name: str,
        producer_role_arns: Optional[list[Output[str]]] = None,
        stack: Optional[str] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        """
        Initialize ChromaDB SQS queues.

        Args:
            name: The unique name of the resource
            producer_role_arns: List of IAM role ARNs that can send messages to
                these queues (e.g., stream processor Lambda role). If None or empty,
                no producer access is granted via queue policy.
            stack: The Pulumi stack name (defaults to current stack)
            opts: Optional resource options
        """
        super().__init__("chromadb:queues:SQSQueues", name, None, opts)

        if stack is None:
            stack = pulumi.get_stack()

        self._producer_role_arns = producer_role_arns or []

        # Create dead letter queues (Standard)
        self.lines_dlq = aws.sqs.Queue(
            f"{name}-lines-dlq",
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

        # Create lines queue (Standard for high throughput)
        self.lines_queue = aws.sqs.Queue(
            f"{name}-lines-queue",
            message_retention_seconds=345600,  # 4 days
            # Visibility timeout must be >= Lambda timeout per AWS requirements.
            visibility_timeout_seconds=120,
            receive_wait_time_seconds=20,  # Long polling
            redrive_policy=Output.all(self.lines_dlq.arn).apply(
                lambda args: json.dumps(
                    {"deadLetterTargetArn": args[0], "maxReceiveCount": 10}
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

        # Create words queue (Standard for high throughput)
        self.words_queue = aws.sqs.Queue(
            f"{name}-words-queue",
            message_retention_seconds=345600,  # 4 days
            visibility_timeout_seconds=120,
            receive_wait_time_seconds=20,  # Long polling
            redrive_policy=Output.all(self.words_dlq.arn).apply(
                lambda args: json.dumps(
                    {"deadLetterTargetArn": args[0], "maxReceiveCount": 10}
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

        # Create receipt summary update dead letter queue
        self.summary_dlq = aws.sqs.Queue(
            f"{name}-summary-dlq",
            message_retention_seconds=1209600,  # 14 days
            visibility_timeout_seconds=300,  # 5 minutes
            receive_wait_time_seconds=0,  # Short polling
            tags={
                "Project": "ChromaDB",
                "Component": "Summary-DLQ",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Create receipt summary update queue
        # Uses delay_seconds=15 to batch multiple label changes for the same receipt
        self.summary_queue = aws.sqs.Queue(
            f"{name}-summary-queue",
            message_retention_seconds=345600,  # 4 days
            visibility_timeout_seconds=120,  # 2x Lambda timeout for safety margin
            receive_wait_time_seconds=20,  # Long polling
            delay_seconds=15,  # 15-second delay for batching multiple changes
            redrive_policy=Output.all(self.summary_dlq.arn).apply(
                lambda args: json.dumps(
                    {"deadLetterTargetArn": args[0], "maxReceiveCount": 3}
                )
            ),
            tags={
                "Project": "ChromaDB",
                "Component": "Summary-Queue",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Create queue policies with least-privilege access
        self.lines_queue_policy = aws.sqs.QueuePolicy(
            f"{name}-lines-queue-policy",
            queue_url=self.lines_queue.url,
            policy=_create_queue_policy_document(
                self.lines_queue.arn, self._producer_role_arns
            ),
            opts=ResourceOptions(parent=self),
        )

        self.words_queue_policy = aws.sqs.QueuePolicy(
            f"{name}-words-queue-policy",
            queue_url=self.words_queue.url,
            policy=_create_queue_policy_document(
                self.words_queue.arn, self._producer_role_arns
            ),
            opts=ResourceOptions(parent=self),
        )

        self.summary_queue_policy = aws.sqs.QueuePolicy(
            f"{name}-summary-queue-policy",
            queue_url=self.summary_queue.url,
            policy=_create_queue_policy_document(
                self.summary_queue.arn, self._producer_role_arns
            ),
            opts=ResourceOptions(parent=self),
        )

        # Export useful properties
        self.lines_queue_url = self.lines_queue.url
        self.lines_queue_arn = self.lines_queue.arn
        self.words_queue_url = self.words_queue.url
        self.words_queue_arn = self.words_queue.arn
        self.summary_queue_url = self.summary_queue.url
        self.summary_queue_arn = self.summary_queue.arn
        self.lines_dlq_arn = self.lines_dlq.arn
        self.words_dlq_arn = self.words_dlq.arn
        self.summary_dlq_arn = self.summary_dlq.arn

        # Register outputs
        self.register_outputs(
            {
                "lines_queue_url": self.lines_queue_url,
                "lines_queue_arn": self.lines_queue_arn,
                "words_queue_url": self.words_queue_url,
                "words_queue_arn": self.words_queue_arn,
                "summary_queue_url": self.summary_queue_url,
                "summary_queue_arn": self.summary_queue_arn,
                "lines_dlq_arn": self.lines_dlq_arn,
                "words_dlq_arn": self.words_dlq_arn,
                "summary_dlq_arn": self.summary_dlq_arn,
            }
        )


def create_chromadb_queues(
    name: str = "chromadb",
    producer_role_arns: Optional[list[Output[str]]] = None,
    opts: Optional[ResourceOptions] = None,
) -> ChromaDBQueues:
    """
    Factory function to create ChromaDB SQS queues.

    Args:
        name: Base name for the resources
        producer_role_arns: List of IAM role ARNs that can send messages to
            these queues (e.g., stream processor Lambda role)
        opts: Optional resource options

    Returns:
        ChromaDBQueues component resource
    """
    return ChromaDBQueues(name, producer_role_arns=producer_role_arns, opts=opts)
