"""Shared AWS client management.

Provides singleton clients for better performance and connection reuse.
"""

import os
from functools import lru_cache
from typing import Optional

import boto3


@lru_cache(maxsize=1)
def get_s3_client():
    """Get or create S3 client (singleton)."""
    return boto3.client(
        "s3", region_name=os.environ.get("AWS_REGION", "us-east-1")
    )


@lru_cache(maxsize=1)
def get_dynamodb_client():
    """Get or create DynamoDB client (singleton)."""
    return boto3.client(
        "dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")
    )


@lru_cache(maxsize=1)
def get_dynamodb_resource():
    """Get or create DynamoDB resource (singleton)."""
    return boto3.resource(
        "dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")
    )


@lru_cache(maxsize=1)
def get_sqs_client():
    """Get or create SQS client (singleton)."""
    return boto3.client(
        "sqs", region_name=os.environ.get("AWS_REGION", "us-east-1")
    )


def get_table(table_name: Optional[str] = None):
    """Get DynamoDB table resource.

    Args:
        table_name: Table name (defaults to DYNAMODB_TABLE_NAME env var)

    Returns:
        DynamoDB table resource
    """
    table_name = table_name or os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        raise ValueError(
            "Table name must be provided or set in DYNAMODB_TABLE_NAME"
        )

    dynamodb = get_dynamodb_resource()
    return dynamodb.Table(table_name)
