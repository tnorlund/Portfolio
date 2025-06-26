"""
AWS utilities for creating and managing SQS queues for ML training jobs.
"""

import json
import logging
from typing import Any, Dict, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def create_standard_queue(
    queue_name: str,
    attributes: Optional[Dict[str, str]] = None,
    tags: Optional[Dict[str, str]] = None,
    region_name: Optional[str] = None,
) -> Optional[str]:
    """
    Create a standard SQS queue.

    Args:
        queue_name: Name of the queue
        attributes: Queue attributes
        tags: Queue tags
        region_name: AWS region

    Returns:
        Queue URL if successful, None otherwise
    """
    sqs = boto3.client("sqs", region_name=region_name)

    if attributes is None:
        attributes = {}

    # Default attributes
    default_attributes = {
        "VisibilityTimeout": "1800",  # 30 minutes
        "MessageRetentionPeriod": "1209600",  # 14 days (maximum)
        "ReceiveMessageWaitTimeSeconds": "20",  # Long polling (max 20 seconds)
    }

    # Merge default attributes with provided attributes
    for key, value in default_attributes.items():
        if key not in attributes:
            attributes[key] = value

    try:
        kwargs = {
            "QueueName": queue_name,
            "Attributes": attributes,
        }

        if tags:
            kwargs["tags"] = tags

        response = sqs.create_queue(**kwargs)
        queue_url = response["QueueUrl"]
        logger.info(f"Created standard queue: {queue_url}")
        return queue_url

    except ClientError as e:
        logger.error(f"Error creating standard queue {queue_name}: {e}")
        return None


def create_fifo_queue(
    queue_name: str,
    attributes: Optional[Dict[str, str]] = None,
    tags: Optional[Dict[str, str]] = None,
    region_name: Optional[str] = None,
) -> Optional[str]:
    """
    Create a FIFO SQS queue.

    Args:
        queue_name: Name of the queue (must end with .fifo)
        attributes: Queue attributes
        tags: Queue tags
        region_name: AWS region

    Returns:
        Queue URL if successful, None otherwise
    """
    # Ensure queue name ends with .fifo
    if not queue_name.endswith(".fifo"):
        queue_name = f"{queue_name}.fifo"

    sqs = boto3.client("sqs", region_name=region_name)

    if attributes is None:
        attributes = {}

    # Default attributes
    default_attributes = {
        "FifoQueue": "true",
        "ContentBasedDeduplication": "true",
        "VisibilityTimeout": "1800",  # 30 minutes
        "MessageRetentionPeriod": "1209600",  # 14 days (maximum)
        "ReceiveMessageWaitTimeSeconds": "20",  # Long polling (max 20 seconds)
    }

    # Merge default attributes with provided attributes
    for key, value in default_attributes.items():
        if key not in attributes:
            attributes[key] = value

    try:
        kwargs = {
            "QueueName": queue_name,
            "Attributes": attributes,
        }

        if tags:
            kwargs["tags"] = tags

        response = sqs.create_queue(**kwargs)
        queue_url = response["QueueUrl"]
        logger.info(f"Created FIFO queue: {queue_url}")
        return queue_url

    except ClientError as e:
        logger.error(f"Error creating FIFO queue {queue_name}: {e}")
        return None


def get_queue_url(
    queue_name: str,
    region_name: Optional[str] = None,
) -> Optional[str]:
    """
    Get URL of an existing SQS queue.

    Args:
        queue_name: Name of the queue
        region_name: AWS region

    Returns:
        Queue URL if successful, None otherwise
    """
    sqs = boto3.client("sqs", region_name=region_name)

    try:
        response = sqs.get_queue_url(QueueName=queue_name)
        return response["QueueUrl"]
    except ClientError as e:
        logger.error(f"Error getting URL for queue {queue_name}: {e}")
        return None


def delete_queue(
    queue_url: str,
    region_name: Optional[str] = None,
) -> bool:
    """
    Delete an SQS queue.

    Args:
        queue_url: URL of the queue
        region_name: AWS region

    Returns:
        True if successful, False otherwise
    """
    sqs = boto3.client("sqs", region_name=region_name)

    try:
        sqs.delete_queue(QueueUrl=queue_url)
        logger.info(f"Deleted queue: {queue_url}")
        return True
    except ClientError as e:
        logger.error(f"Error deleting queue {queue_url}: {e}")
        return False


def create_queue_with_dlq(
    queue_name: str,
    dlq_name: Optional[str] = None,
    fifo: bool = False,
    max_receives: int = 5,
    attributes: Optional[Dict[str, str]] = None,
    dlq_attributes: Optional[Dict[str, str]] = None,
    tags: Optional[Dict[str, str]] = None,
    region_name: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Create an SQS queue with a Dead Letter Queue.

    Args:
        queue_name: Name of the main queue
        dlq_name: Name of the DLQ (defaults to queue_name + "-dlq")
        fifo: Whether to create FIFO queues
        max_receives: Maximum number of receives before message is moved to DLQ
        attributes: Main queue attributes
        dlq_attributes: DLQ attributes
        tags: Queue tags
        region_name: AWS region

    Returns:
        Tuple of (queue_url, dlq_url) if successful, (None, None) otherwise
    """
    # Generate DLQ name if not provided
    if dlq_name is None:
        dlq_name = f"{queue_name}-dlq"

    # Ensure FIFO naming if needed
    if fifo:
        if not queue_name.endswith(".fifo"):
            queue_name = f"{queue_name}.fifo"
        if not dlq_name.endswith(".fifo"):
            dlq_name = f"{dlq_name}.fifo"

    # Create default attributes if not provided
    if attributes is None:
        attributes = {}
    if dlq_attributes is None:
        dlq_attributes = {}

    # Create the DLQ first
    dlq_url = None
    if fifo:
        dlq_url = create_fifo_queue(dlq_name, dlq_attributes, tags, region_name)
    else:
        dlq_url = create_standard_queue(dlq_name, dlq_attributes, tags, region_name)

    if not dlq_url:
        logger.error(f"Failed to create DLQ {dlq_name}")
        return None, None

    # Get the DLQ ARN
    sqs = boto3.client("sqs", region_name=region_name)
    try:
        response = sqs.get_queue_attributes(
            QueueUrl=dlq_url, AttributeNames=["QueueArn"]
        )
        dlq_arn = response["Attributes"]["QueueArn"]
    except ClientError as e:
        logger.error(f"Error getting ARN for DLQ {dlq_name}: {e}")
        return None, None

    # Configure redrive policy for the main queue
    redrive_policy = {
        "deadLetterTargetArn": dlq_arn,
        "maxReceiveCount": str(max_receives),
    }

    # Add redrive policy to attributes
    attributes["RedrivePolicy"] = json.dumps(redrive_policy)

    # Create the main queue
    queue_url = None
    if fifo:
        queue_url = create_fifo_queue(queue_name, attributes, tags, region_name)
    else:
        queue_url = create_standard_queue(queue_name, attributes, tags, region_name)

    if not queue_url:
        logger.error(f"Failed to create main queue {queue_name}")
        # Clean up the DLQ that was created
        delete_queue(dlq_url, region_name)
        return None, None

    logger.info(f"Created queue {queue_name} with DLQ {dlq_name}")
    return queue_url, dlq_url


def purge_queue(
    queue_url: str,
    region_name: Optional[str] = None,
) -> bool:
    """
    Purge all messages from an SQS queue.

    Args:
        queue_url: URL of the queue
        region_name: AWS region

    Returns:
        True if successful, False otherwise
    """
    sqs = boto3.client("sqs", region_name=region_name)

    try:
        sqs.purge_queue(QueueUrl=queue_url)
        logger.info(f"Purged queue: {queue_url}")
        return True
    except ClientError as e:
        logger.error(f"Error purging queue {queue_url}: {e}")
        return False
