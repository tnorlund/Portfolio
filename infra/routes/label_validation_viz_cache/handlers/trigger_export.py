import json
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3
import urllib3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

LANGSMITH_API_URL = "https://api.smith.langchain.com"


def _ensure_destination_exists(setup_lambda_name):
    """Get destination from setup Lambda (which handles SSM caching)."""
    lambda_client = boto3.client("lambda")

    # Just invoke the setup Lambda - it handles SSM caching internally
    # with a unique parameter path per component
    logger.info("Invoking setup lambda: %s", setup_lambda_name)
    response = lambda_client.invoke(
        FunctionName=setup_lambda_name,
        InvocationType="RequestResponse",
        Payload=json.dumps({"prefix": "traces"}),
    )

    result = json.loads(response["Payload"].read().decode())
    if result.get("statusCode") != 200:
        raise Exception(f"Setup lambda failed: {result}")

    destination_id = result.get("destination_id")
    if not destination_id:
        raise Exception(f"No destination_id returned: {result}")

    logger.info("Got destination from setup lambda: %s", destination_id)
    return destination_id


def handler(event, context):
    """Trigger LangSmith bulk export for the configured project."""
    logger.info("Received event: %s", json.dumps(event))

    langchain_project = event.get("langchain_project") or os.environ.get(
        "LANGSMITH_PROJECT_NAME"
    )
    if not langchain_project:
        raise ValueError(
            "langchain_project must be provided in event or LANGSMITH_PROJECT_NAME env var"
        )
    api_key = os.environ["LANGCHAIN_API_KEY"]
    tenant_id = os.environ.get("LANGSMITH_TENANT_ID")
    setup_lambda_name = os.environ.get("SETUP_LAMBDA_NAME")

    http = urllib3.PoolManager()

    headers = {"x-api-key": api_key}
    if tenant_id:
        headers["x-tenant-id"] = tenant_id

    # Get destination from setup Lambda (handles SSM caching)
    destination_id = _ensure_destination_exists(setup_lambda_name)

    # Get project_id from LangSmith
    response = http.request(
        "GET",
        f"{LANGSMITH_API_URL}/api/v1/sessions",
        headers=headers,
    )
    if response.status != 200:
        raise Exception(f"Failed to list projects: {response.data.decode()}")

    projects = json.loads(response.data.decode("utf-8"))
    project_id = None
    for proj in projects:
        if proj.get("name") == langchain_project:
            project_id = proj.get("id")
            break

    if not project_id:
        raise Exception(f"Project not found: {langchain_project}")

    logger.info("Found project_id: %s for %s", project_id, langchain_project)

    # Trigger bulk export.
    # Event can override window; default remains a 7-day lookback.
    days_back = event.get("days_back", 7)
    end_time_value = event.get("end_time")
    if end_time_value:
        end_time = datetime.fromisoformat(
            end_time_value.replace("Z", "+00:00")
        )
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
    else:
        end_time = datetime.now(timezone.utc)
    end_time_iso = end_time.astimezone(timezone.utc).isoformat()
    start_time_iso = event.get("start_time")
    if not start_time_iso:
        start_time_iso = (end_time - timedelta(days=days_back)).isoformat()

    export_fields = event.get(
        "export_fields",
        [
            "id",
            "trace_id",
            "name",
            "outputs",
            "extra",
            "start_time",
            "end_time",
        ],
    )

    export_body = {
        "bulk_export_destination_id": destination_id,
        "session_id": project_id,
        "start_time": start_time_iso,
        "end_time": end_time_iso,
        "export_fields": export_fields,
    }

    post_headers = dict(headers)
    post_headers["Content-Type"] = "application/json"

    response = http.request(
        "POST",
        f"{LANGSMITH_API_URL}/api/v1/bulk-exports",
        headers=post_headers,
        body=json.dumps(export_body),
    )

    if response.status not in (200, 201, 202):
        raise Exception(f"Failed to trigger export: {response.data.decode()}")

    result = json.loads(response.data.decode("utf-8"))
    export_id = result.get("id")
    logger.info("Triggered export: %s", export_id)

    return {
        "export_id": export_id,
        "status": result.get("status", "pending"),
    }
