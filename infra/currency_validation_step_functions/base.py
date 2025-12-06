"""Base configuration and utilities for embedding components."""

import pulumi
from pulumi import Config

# Shared configuration
config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")
ollama_api_key = config.require_secret("OLLAMA_API_KEY")
langsmith_api_key = config.require_secret("LANGCHAIN_API_KEY")
stack = pulumi.get_stack()

# Import the existing Lambda layer for receipt packages
try:
    # pylint: disable=import-error
    from infra.components.lambda_layer import dynamo_layer  # type: ignore[import-not-found]
    from infra.components.lambda_layer import label_layer  # type: ignore[import-not-found]

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
    "ollama_api_key",
    "langsmith_api_key",
    "stack",
    "dynamo_layer",
    "dynamodb_table",
]
