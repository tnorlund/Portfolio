"""
Configuration settings for the jobs package.

This module provides centralized configuration settings for the jobs package,
ensuring consistency between CLI and processing components.
"""

import os
from typing import Dict, Any, Optional

# DynamoDB table name
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "receipt-processing")

# Default AWS region
DEFAULT_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Default job settings
DEFAULT_JOB_PRIORITY = "medium"

# SQS queue settings
DEFAULT_MAX_RECEIVES = 5
DEFAULT_VISIBILITY_TIMEOUT = 1800  # 30 minutes

# Instance settings
DEFAULT_INSTANCE_STATUS = "running"

# Retry settings
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 30  # seconds

# Status values
JOB_STATUS_PENDING = "pending"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_SUCCEEDED = "succeeded"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_CANCELLED = "cancelled"
JOB_STATUS_INTERRUPTED = "interrupted"

# Priority values
JOB_PRIORITY_LOW = "low"
JOB_PRIORITY_MEDIUM = "medium"
JOB_PRIORITY_HIGH = "high"
JOB_PRIORITY_CRITICAL = "critical"


# Function to get configuration with overrides
def get_config(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Get configuration values with optional overrides.

    Args:
        overrides: Dictionary of override values

    Returns:
        Dictionary of configuration values
    """
    config = {
        "dynamodb_table": DYNAMODB_TABLE,
        "region": DEFAULT_REGION,
        "default_job_priority": DEFAULT_JOB_PRIORITY,
        "max_receives": DEFAULT_MAX_RECEIVES,
        "visibility_timeout": DEFAULT_VISIBILITY_TIMEOUT,
        "default_instance_status": DEFAULT_INSTANCE_STATUS,
        "max_retries": DEFAULT_MAX_RETRIES,
        "retry_delay": DEFAULT_RETRY_DELAY,
    }

    if overrides:
        config.update(overrides)

    return config
