"""
Local configuration for development and testing.

This configuration enables running the agent labeling system locally
without AWS dependencies or API costs.
"""

import os
from typing import Any, Dict, Optional


class LocalConfig:
    """Configuration for local development."""

    # Service endpoints
    DYNAMO_ENDPOINT = os.getenv("DYNAMO_ENDPOINT", "http://localhost:8000")
    LOCALSTACK_ENDPOINT = os.getenv(
        "LOCALSTACK_ENDPOINT", "http://localhost:4566"
    )

    # Mock settings
    MOCK_OPENAI = os.getenv("MOCK_OPENAI", "true").lower() == "true"
    MOCK_PINECONE = os.getenv("MOCK_PINECONE", "true").lower() == "true"
    MOCK_PLACES_API = os.getenv("MOCK_PLACES_API", "true").lower() == "true"

    # Feature flags
    PATTERN_DETECTION_ONLY = (
        os.getenv("PATTERN_DETECTION_ONLY", "false").lower() == "true"
    )
    SKIP_EMBEDDINGS = os.getenv("SKIP_EMBEDDINGS", "true").lower() == "true"
    STORE_LABELS = os.getenv("STORE_LABELS", "false").lower() == "true"

    # Performance settings
    PATTERN_DETECTION_TIMEOUT = float(
        os.getenv("PATTERN_DETECTION_TIMEOUT", "0.2")
    )  # 200ms
    MAX_CONCURRENT_PATTERNS = int(os.getenv("MAX_CONCURRENT_PATTERNS", "5"))

    # Batch processing
    BATCH_SIZE_THRESHOLD = int(os.getenv("BATCH_SIZE_THRESHOLD", "5"))
    MAX_BATCH_SIZE = int(os.getenv("MAX_BATCH_SIZE", "50"))

    # Local DynamoDB table
    DYNAMO_TABLE_NAME = os.getenv("DYNAMO_TABLE_NAME", "portfolio-metadata")

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_PERFORMANCE_METRICS = (
        os.getenv("LOG_PERFORMANCE_METRICS", "true").lower() == "true"
    )

    @classmethod
    def to_dict(cls) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return {
            "dynamo_endpoint": cls.DYNAMO_ENDPOINT,
            "localstack_endpoint": cls.LOCALSTACK_ENDPOINT,
            "mock_openai": cls.MOCK_OPENAI,
            "mock_pinecone": cls.MOCK_PINECONE,
            "mock_places_api": cls.MOCK_PLACES_API,
            "pattern_detection_only": cls.PATTERN_DETECTION_ONLY,
            "skip_embeddings": cls.SKIP_EMBEDDINGS,
            "store_labels": cls.STORE_LABELS,
            "pattern_detection_timeout": cls.PATTERN_DETECTION_TIMEOUT,
            "max_concurrent_patterns": cls.MAX_CONCURRENT_PATTERNS,
            "batch_size_threshold": cls.BATCH_SIZE_THRESHOLD,
            "max_batch_size": cls.MAX_BATCH_SIZE,
            "dynamo_table_name": cls.DYNAMO_TABLE_NAME,
            "log_level": cls.LOG_LEVEL,
            "log_performance_metrics": cls.LOG_PERFORMANCE_METRICS,
        }

    @classmethod
    def is_local_mode(cls) -> bool:
        """Check if running in local mode."""
        return os.getenv("ENVIRONMENT", "").lower() in ["local", "test", ""]


def get_local_client_config() -> Optional[Dict[str, Any]]:
    """Get client configuration for local development."""
    if not LocalConfig.is_local_mode():
        return None

    config = {
        "environment": "local",
        "dynamo_config": {
            "endpoint_url": LocalConfig.DYNAMO_ENDPOINT,
            "region_name": "us-east-1",
            "aws_access_key_id": "dummy",
            "aws_secret_access_key": "dummy",
        },
        "s3_config": {
            "endpoint_url": LocalConfig.LOCALSTACK_ENDPOINT,
            "region_name": "us-east-1",
            "aws_access_key_id": "test",
            "aws_secret_access_key": "test",
        },
        "sns_config": {
            "endpoint_url": LocalConfig.LOCALSTACK_ENDPOINT,
            "region_name": "us-east-1",
            "aws_access_key_id": "test",
            "aws_secret_access_key": "test",
        },
    }

    # Add mock flags
    if LocalConfig.MOCK_OPENAI:
        config["mock_openai"] = True
    if LocalConfig.MOCK_PINECONE:
        config["mock_pinecone"] = True
    if LocalConfig.MOCK_PLACES_API:
        config["mock_places_api"] = True

    return config
