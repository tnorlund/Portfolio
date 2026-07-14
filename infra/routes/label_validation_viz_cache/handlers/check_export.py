import json
import logging
import os

import urllib3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

LANGSMITH_API_URL = "https://api.smith.langchain.com"


def handler(event, context):
    """Check LangSmith export status."""
    logger.info("Received event: %s", json.dumps(event))

    export_id = event.get("export_id")
    if not export_id:
        raise ValueError("export_id is required")

    api_key = os.environ["LANGCHAIN_API_KEY"]

    http = urllib3.PoolManager()

    response = http.request(
        "GET",
        f"{LANGSMITH_API_URL}/api/v1/bulk-exports/{export_id}",
        headers={"x-api-key": api_key},
    )

    if response.status != 200:
        logger.error("Failed to check export: %s", response.data.decode())
        return {"status": "error", "export_id": export_id}

    result = json.loads(response.data.decode("utf-8"))
    status = result.get("status", "unknown")

    # Normalize status
    if status in ("Complete", "Completed"):
        status = "completed"
    elif status in ("Failed", "Cancelled"):
        status = "failed"
    elif status in ("Pending", "Running", "InProgress"):
        status = "pending"

    logger.info("Export %s status: %s", export_id, status)

    return {
        "export_id": export_id,
        "status": status,
    }
