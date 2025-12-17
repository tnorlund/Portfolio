"""Centralized configuration for unified embedding Lambda functions.

All Lambda configurations, environment variables, and settings in one place.
"""

import os
from typing import Any, Dict, List, cast

# Constants
GIGABYTE = 1024
MINUTE = 60


# Common environment variables (merged with handler-specific ones)
COMMON_ENV_VARS: Dict[str, str | None] = {
    "DYNAMODB_TABLE_NAME": os.environ.get("DYNAMODB_TABLE_NAME"),
    "CHROMADB_BUCKET": os.environ.get("CHROMADB_BUCKET"),
    "COMPACTION_QUEUE_URL": os.environ.get("COMPACTION_QUEUE_URL"),
    "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY"),
    "S3_BUCKET": os.environ.get("S3_BUCKET"),
}


# Runtime configuration
IS_LOCAL = os.environ.get("IS_LOCAL", "false").lower() == "true"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
