"""Centralized configuration for unified embedding Lambda functions.

All Lambda configurations, environment variables, and settings in one place.
"""

import os
from typing import Dict, Any, List, cast

# Constants
GIGABYTE = 1024
MINUTE = 60


# Helper to express memory/ephemeral storage in MiB (AWS expects MiB integers).
# Example: GiB(0.5) == 512
def GiB(n: float | int) -> int:
    return int(n * 1024)

# Lambda function configurations
LAMBDA_CONFIGS = {
    "word_polling": {
        "name": "word-poll",
        "memory": GiB(2),
        "timeout": MINUTE * 15,
        "ephemeral_storage": GiB(2),
        "description": "Poll OpenAI for word embedding batch results",
        "env_vars": {
            "HANDLER_TYPE": "word_polling",
            "CHROMA_PERSIST_DIRECTORY": "/tmp/chroma",
        },
    },
    "line_polling": {
        "name": "line-poll",
        "memory": GiB(2),
        "timeout": MINUTE * 15,
        "ephemeral_storage": GiB(2),
        "description": "Poll OpenAI for line embedding batch results",
        "env_vars": {
            "HANDLER_TYPE": "line_polling",
            "CHROMA_PERSIST_DIRECTORY": "/tmp/chroma",
            "SKIP_TABLE_VALIDATION": "true",
        },
    },
    "compaction": {
        "name": "compact",
        "memory": GiB(3),
        "timeout": MINUTE * 15,
        "ephemeral_storage": GiB(3),
        "description": "Compact ChromaDB deltas into snapshots",
        "env_vars": {
            "HANDLER_TYPE": "compaction",
            "CHROMA_PERSIST_DIRECTORY": "/tmp/chroma",
            "CHUNK_SIZE": "10",  # Tunable: Controls deltas per Lambda
            "HEARTBEAT_INTERVAL_SECONDS": "60",
            "LOCK_DURATION_MINUTES": "5",
            "DELETE_PROCESSED_DELTAS": "false",
            "DELETE_INTERMEDIATE_CHUNKS": "true",
        },
    },
    "find_unembedded": {
        "name": "find-unembedded",
        "memory": GiB(1),
        "timeout": MINUTE * 15,
        "ephemeral_storage": GiB(0.5),
        "description": "Find items without embeddings",
        "env_vars": {
            "HANDLER_TYPE": "find_unembedded",
        },
    },
    "submit_openai": {
        "name": "submit-openai",
        "memory": GiB(1),
        "timeout": MINUTE * 15,
        "ephemeral_storage": GiB(0.5),
        "description": "Submit embedding batches to OpenAI",
        "env_vars": {
            "HANDLER_TYPE": "submit_openai",
        },
    },
    "list_pending": {
        "name": "list-pending",
        "memory": GiB(0.5),
        "timeout": MINUTE * 15,
        "ephemeral_storage": GiB(0.5),
        "description": "List pending embedding batches",
        "env_vars": {
            "HANDLER_TYPE": "list_pending",
        },
    },
    "split_into_chunks": {
        "name": "split-into-chunks",
        "memory": GiB(0.5),
        "timeout": MINUTE,
        "ephemeral_storage": GiB(0.5),
        "description": "Split delta results into chunks for parallel proc",
        "env_vars": {
            "HANDLER_TYPE": "split_into_chunks",
        },
    },
    "find_unembedded_words": {
        "name": "find-unembedded-words",
        "memory": GiB(1),
        "timeout": MINUTE * 15,
        "ephemeral_storage": GiB(0.5),
        "description": "Find words without embeddings",
        "env_vars": {
            "HANDLER_TYPE": "find_unembedded_words",
        },
    },
    "submit_words_openai": {
        "name": "submit-words-openai",
        "memory": GiB(1),
        "timeout": MINUTE * 15,
        "ephemeral_storage": GiB(0.5),
        "description": "Submit word embedding batches to OpenAI",
        "env_vars": {
            "HANDLER_TYPE": "submit_words_openai",
        },
    },
}

# Common environment variables (merged with handler-specific ones)
COMMON_ENV_VARS: Dict[str, str | None] = {
    "DYNAMODB_TABLE_NAME": os.environ.get("DYNAMODB_TABLE_NAME"),
    "CHROMADB_BUCKET": os.environ.get("CHROMADB_BUCKET"),
    "COMPACTION_QUEUE_URL": os.environ.get("COMPACTION_QUEUE_URL"),
    "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY"),
    "S3_BUCKET": os.environ.get("S3_BUCKET"),
}


def get_lambda_config(handler_type: str) -> Dict[str, Any]:
    """Get configuration for a specific Lambda handler.

    Args:
        handler_type: The type of handler

    Returns:
        Lambda configuration dictionary

    Raises:
        KeyError: If handler_type is not found
    """
    if handler_type not in LAMBDA_CONFIGS:
        raise KeyError(f"Unknown handler type: {handler_type}")

    config = LAMBDA_CONFIGS[handler_type].copy()

    # Merge common env vars with handler-specific ones
    common_vars: Dict[str, str] = {
        k: v for k, v in COMMON_ENV_VARS.items() if v is not None
    }
    handler_vars: Dict[str, str] = cast(Dict[str, str], config["env_vars"])
    config["env_vars"] = {
        **common_vars,
        **handler_vars,
    }

    return config


def get_all_handler_types() -> List[str]:
    """Get list of all available handler types."""
    return list(LAMBDA_CONFIGS.keys())


# Runtime configuration
IS_LOCAL = os.environ.get("IS_LOCAL", "false").lower() == "true"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
