"""Lambda-specific configuration and model activation for the receipt MCP."""

import json
import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from receipt_dynamo import DynamoClient

logger = logging.getLogger(__name__)


def load_config() -> dict[str, Any]:
    """Read Lambda configuration without invoking the local Pulumi CLI."""
    return {
        key: os.environ[env_key]
        for env_key, key in (
            ("DYNAMODB_TABLE_NAME", "dynamodb_table_name"),
            ("PORTFOLIO_ENV", "portfolio_env"),
            ("OPENAI_API_KEY", "openai_api_key"),
            ("OPENROUTER_API_KEY", "openrouter_api_key"),
            ("LANGCHAIN_API_KEY", "langchain_api_key"),
            ("GOOGLE_PLACES_API_KEY", "google_places_api_key"),
        )
        if os.environ.get(env_key)
    }


async def set_active_model(
    dynamo_client: "DynamoClient", job_name: str
) -> dict[str, Any]:
    """Keep Lambda's tag-only activation; S3 promotion belongs to the CLI.

    The Lambda has neither a training bucket setting nor permission to
    publish CoreML pointers. Preserve its existing tool behavior separately
    from the local server's exported-bundle promotion.
    """
    try:
        matching_jobs, _ = dynamo_client.get_job_by_name(job_name)
        if not matching_jobs:
            return {"error": f"No job found with name: {job_name}"}
        job = matching_jobs[0]

        old_active = dynamo_client.get_active_model_job()
        if old_active:
            old_active.tags = {
                key: value
                for key, value in (old_active.tags or {}).items()
                if key != "active_model"
            }
            dynamo_client.update_job(old_active)

        job.tags = {**(job.tags or {}), "active_model": "true"}
        dynamo_client.update_job(job)

        raw_results: dict[str, Any] | str = job.results or {}
        results = (
            json.loads(raw_results)
            if isinstance(raw_results, str)
            else raw_results
        )

        return {
            "success": True,
            "name": job.name,
            "job_id": job.job_id,
            "best_f1": results.get("best_f1"),
            "message": f"Set {job.name} as the active model",
        }
    except Exception as error:
        logger.exception("Error setting active model")
        return {"error": str(error)}
