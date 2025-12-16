"""Base configuration and utilities for embedding components."""

import pulumi
from pulumi import Config

# Shared configuration
config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")
stack = pulumi.get_stack()

# Import the existing Dynamo layer for receipt packages
try:
    # pylint: disable=import-error
    from lambda_layer import dynamo_layer  # type: ignore[import-not-found]

    # pylint: enable=import-error
except ImportError:
    dynamo_layer = None

# Import shared resources
# pylint: disable=import-error
from dynamo_db import dynamodb_table  # type: ignore[import-not-found]

# pylint: enable=import-error

__all__ = [
    "config",
    "openai_api_key",
    "stack",
    "dynamo_layer",
    "dynamodb_table",
]
