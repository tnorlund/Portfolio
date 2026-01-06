"""
Trigger Lambda for LangSmith bulk export.

This Lambda triggers a bulk export job to export traces from LangSmith to S3.
It can be invoked manually or scheduled.

The export job runs asynchronously in LangSmith's infrastructure.
Parquet files will appear in the export bucket once the job completes.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
import urllib3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# LangSmith API endpoint
LANGSMITH_API_URL = "https://api.smith.langchain.com"


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Trigger a LangSmith bulk export job.

    Environment variables:
        LANGCHAIN_API_KEY: LangSmith API key
        LANGSMITH_PROJECT: LangSmith project name
        STACK: Pulumi stack name (dev/prod)

    Event parameters (optional):
        days_back: Number of days to export (default: 7)
        project_id: Override project ID (default: from env)

    Returns:
        Dict with export_id and status
    """
    api_key = os.environ["LANGCHAIN_API_KEY"]
    tenant_id = os.environ["LANGSMITH_TENANT_ID"]
    project_name = os.environ["LANGSMITH_PROJECT"]
    stack = os.environ["STACK"]

    # Get parameters from event or use defaults
    days_back = event.get("days_back", 7)
    project_id = event.get("project_id")

    logger.info(f"Triggering bulk export for project: {project_name}")
    logger.info(f"Days back: {days_back}")

    # Get destination_id from SSM
    ssm = boto3.client("ssm")
    param_name = f"/langsmith/{stack}/destination_id"

    try:
        response = ssm.get_parameter(Name=param_name)
        destination_id = response["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        logger.error(f"Destination not found in SSM: {param_name}")
        return {
            "statusCode": 400,
            "message": "Destination not registered. Run setup Lambda first.",
        }

    logger.info(f"Using destination_id: {destination_id}")

    # If project_id not provided, we need to look it up by name
    http = urllib3.PoolManager()

    if not project_id:
        # List projects to find the ID by name
        try:
            response = http.request(
                "GET",
                f"{LANGSMITH_API_URL}/api/v1/sessions",
                headers={
                    "x-api-key": api_key,
                    "X-Tenant-Id": tenant_id,
                },
            )

            if response.status != 200:
                error_msg = response.data.decode("utf-8")
                logger.error(f"Failed to list projects: {error_msg}")
                return {
                    "statusCode": response.status,
                    "message": f"Failed to list projects: {error_msg}",
                }

            projects = json.loads(response.data.decode("utf-8"))
            for proj in projects:
                if proj.get("name") == project_name:
                    project_id = proj.get("id")
                    break

            if not project_id:
                logger.error(f"Project not found: {project_name}")
                return {
                    "statusCode": 404,
                    "message": f"Project not found: {project_name}",
                }

        except Exception as e:
            logger.exception("Error looking up project")
            return {
                "statusCode": 500,
                "message": f"Error looking up project: {str(e)}",
            }

    logger.info(f"Using project_id: {project_id}")

    # Calculate date range
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days_back)

    logger.info(f"Export range: {start_time.isoformat()} to {end_time.isoformat()}")

    # Trigger bulk export
    try:
        response = http.request(
            "POST",
            f"{LANGSMITH_API_URL}/api/v1/bulk-exports",
            headers={
                "x-api-key": api_key,
                "X-Tenant-Id": tenant_id,
                "Content-Type": "application/json",
            },
            body=json.dumps({
                "destination_id": destination_id,
                "session_id": project_id,  # LangSmith uses session_id for project
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                # Only export fields we need for visualization
                "export_fields": [
                    "id",
                    "name",
                    "outputs",
                    "metadata",
                    "parent_run_id",
                    "start_time",
                    "end_time",
                    "run_type",
                    "status",
                ],
            }),
        )

        if response.status not in (200, 201, 202):
            error_msg = response.data.decode("utf-8")
            logger.error(f"Failed to trigger export: {response.status} - {error_msg}")
            return {
                "statusCode": response.status,
                "message": f"Failed to trigger export: {error_msg}",
            }

        result = json.loads(response.data.decode("utf-8"))
        export_id = result.get("id")

        logger.info(f"Export triggered successfully: {export_id}")
        logger.info(f"Export status: {result.get('status')}")

        return {
            "statusCode": 200,
            "message": "Export triggered successfully",
            "export_id": export_id,
            "status": result.get("status"),
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
        }

    except Exception as e:
        logger.exception("Error triggering export")
        return {
            "statusCode": 500,
            "message": f"Error: {str(e)}",
        }
