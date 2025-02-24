"""AWS integration utilities for Receipt Trainer."""

from typing import Dict, Any, Optional
import logging
import boto3
from dynamo.data._pulumi import load_env as _load_pulumi_env

logger = logging.getLogger(__name__)


def load_env(env: str = "dev") -> Dict[str, Any]:
    """Load Pulumi stack outputs for the given environment.

    Args:
        env: Pulumi stack environment (dev/prod)

    Returns:
        Dictionary of stack outputs
    """
    try:
        return _load_pulumi_env(env)
    except Exception as e:
        logger.error(f"Failed to load Pulumi stack outputs for environment {env}: {e}")
        raise


def get_dynamo_table(table_name: Optional[str] = None, env: str = "dev") -> str:
    """Get DynamoDB table name from Pulumi stack or direct input.

    Args:
        table_name: Optional explicit table name
        env: Pulumi stack environment if table_name not provided

    Returns:
        DynamoDB table name
    """
    if table_name:
        return table_name

    stack_outputs = load_env(env)
    table_name = stack_outputs.get("dynamodb_table_name")

    if not table_name:
        raise ValueError(
            f"Could not find DynamoDB table name in Pulumi stack outputs for environment: {env}"
        )

    return table_name


def get_s3_bucket(bucket_name: Optional[str] = None, env: str = "dev") -> str:
    """Get S3 bucket name from Pulumi stack or direct input.

    Args:
        bucket_name: Optional explicit bucket name
        env: Pulumi stack environment if bucket_name not provided

    Returns:
        S3 bucket name
    """
    if bucket_name:
        return bucket_name

    stack_outputs = load_env(env)
    bucket_name = stack_outputs.get("s3_bucket_name")

    if not bucket_name:
        raise ValueError(
            f"Could not find S3 bucket name in Pulumi stack outputs for environment: {env}"
        )

    return bucket_name
