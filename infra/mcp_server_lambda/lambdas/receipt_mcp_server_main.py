"""
Lambda-aware entry point for ``python -m receipt_mcp_server``.

Monkey-patches ``receipt_dynamo.data._pulumi.load_env`` and
``load_secrets`` so the server reads configuration from Lambda
environment variables instead of shelling out to the Pulumi CLI.
Then delegates to the original server ``main()``.
"""

import os


def _load_env_from_envvars(**_kwargs):
    """Return a config dict built from Lambda environment variables."""
    config = {}
    env_mapping = {
        "DYNAMODB_TABLE_NAME": "dynamodb_table_name",
        "PORTFOLIO_ENV": "portfolio_env",
    }
    for env_key, config_key in env_mapping.items():
        val = os.environ.get(env_key)
        if val:
            config[config_key] = val
    return config


def _load_secrets_from_envvars(**_kwargs):
    """Return a secrets dict built from Lambda environment variables."""
    secrets = {}
    for env_key, secret_key in (
        ("OPENAI_API_KEY", "portfolio:OPENAI_API_KEY"),
        ("CHROMA_CLOUD_API_KEY", "portfolio:CHROMA_CLOUD_API_KEY"),
        ("CHROMA_CLOUD_TENANT", "portfolio:CHROMA_CLOUD_TENANT"),
        ("CHROMA_CLOUD_DATABASE", "portfolio:CHROMA_CLOUD_DATABASE"),
        ("OPENROUTER_API_KEY", "portfolio:OPENROUTER_API_KEY"),
        ("LANGCHAIN_API_KEY", "portfolio:LANGCHAIN_API_KEY"),
        ("GOOGLE_PLACES_API_KEY", "portfolio:GOOGLE_PLACES_API_KEY"),
    ):
        val = os.environ.get(env_key)
        if val:
            secrets[secret_key] = val
    # Chroma Cloud enabled flag (always true in Lambda)
    secrets["portfolio:chroma_cloud_enabled"] = "true"
    return secrets


# Patch before the server module is imported
import receipt_dynamo.data._pulumi as _pulumi_mod  # noqa: E402

_pulumi_mod.load_env = _load_env_from_envvars
_pulumi_mod.load_secrets = _load_secrets_from_envvars

# Now import and run the server
import asyncio  # noqa: E402

from receipt_mcp_server.server import main  # noqa: E402

asyncio.run(main())
