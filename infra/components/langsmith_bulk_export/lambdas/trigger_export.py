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


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Trigger a LangSmith bulk export job.

    Environment variables (used as defaults):
        LANGCHAIN_API_KEY: LangSmith API key
        LANGSMITH_TENANT_ID: LangSmith workspace/tenant ID
        LANGSMITH_PROJECT: LangSmith project name
        STACK: Pulumi stack name (dev/prod)

    Event parameters (override environment variables):
        tenant_id: LangSmith workspace ID (overrides LANGSMITH_TENANT_ID)
        project_name: LangSmith project name (overrides LANGSMITH_PROJECT)
        project_id: Direct project ID (skips name lookup)
        destination_id: S3 destination ID (overrides SSM lookup)
        days_back: Number of days to export (default: 7)
        export_fields: List of fields to export (default: standard set)

    Returns:
        Dict with export_id and status
    """
    # Get config from event or fall back to environment variables
    api_key = event.get("api_key") or os.environ["LANGCHAIN_API_KEY"]
    tenant_id = event.get("tenant_id") or os.environ.get("LANGSMITH_TENANT_ID")
    project_name = event.get("project_name") or os.environ.get("LANGSMITH_PROJECT")
    stack = os.environ.get("STACK", "dev")

    # Get parameters from event or use defaults
    days_back = event.get("days_back", 7)
    project_id = event.get("project_id")
    destination_id = event.get("destination_id")

    if not tenant_id:
        return {
            "statusCode": 400,
            "message": "tenant_id required (via event or LANGSMITH_TENANT_ID env var)",
        }

    logger.info(f"Triggering bulk export for project: {project_name or project_id}")
    logger.info(f"Tenant ID: {tenant_id}")
    logger.info(f"Days back: {days_back}")

    # Get destination_id from event, SSM, or fail
    if not destination_id:
        ssm = boto3.client("ssm")
        param_name = f"/langsmith/{stack}/destination_id"

        try:
            response = ssm.get_parameter(Name=param_name)
            destination_id = response["Parameter"]["Value"]
        except ssm.exceptions.ParameterNotFound:
            logger.warning("Destination not found in SSM: %s", param_name)
            return {
                "statusCode": 400,
                "message": "destination_id required (via event or SSM parameter). Run setup Lambda first.",
            }
        except Exception:
            logger.exception("Unexpected error retrieving destination from SSM: %s", param_name)
            return {
                "statusCode": 500,
                "message": "Error retrieving destination_id from SSM",
            }

    logger.info(f"Using destination_id: {destination_id}")

    # If project_id not provided, we need to look it up by name
    # Use timeout to prevent indefinite hangs in Lambda environment
    http = urllib3.PoolManager(timeout=urllib3.Timeout(connect=5.0, read=30.0))

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
                "message": f"Error looking up project: {e!s}",
            }

    logger.info(f"Using project_id: {project_id}")

    # Calculate date range
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days_back)

    logger.info(f"Export range: {start_time.isoformat()} to {end_time.isoformat()}")

    # Default export fields for visualization
    default_export_fields = [
        "id",
        "name",
        "outputs",
        "extra",  # Contains metadata
        "parent_run_id",
        "start_time",
        "end_time",
        "run_type",
        "status",
    ]
    export_fields = event.get("export_fields", default_export_fields)

    # Trigger bulk export
    try:
        export_body = {
            "bulk_export_destination_id": destination_id,
            "session_id": project_id,  # LangSmith uses session_id for project
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
        }
        # Only include export_fields if specified (omitting uses all fields)
        if export_fields:
            export_body["export_fields"] = export_fields

        logger.info(f"Export body: {json.dumps(export_body)}")

        response = http.request(
            "POST",
            f"{LANGSMITH_API_URL}/api/v1/bulk-exports",
            headers={
                "x-api-key": api_key,
                "X-Tenant-Id": tenant_id,
                "Content-Type": "application/json",
            },
            body=json.dumps(export_body),
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
            "message": f"Error: {e!s}",
        }
