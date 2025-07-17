"""MCP server with clean output and no logging."""

import logging
import os
import json
import subprocess
from pathlib import Path
from typing import Dict, Any

from mcp.server import FastMCP
from botocore.exceptions import ClientError

try:
    # For pinecone-client v3.x+
    from pinecone.exceptions import ApiException
except ImportError:
    # Fallback for older versions
    from pinecone.core.client.exceptions import ApiException


# Disable all logging to keep output clean
logging.disable(logging.CRITICAL)

# Create the MCP server
mcp = FastMCP("receipt-validation")

# Lazy imports and initialization
_initialized = False
_config: Dict[str, Any] = {}
_clients: Dict[str, Any] = {}


def _lazy_init():
    """Lazy initialization of all dependencies."""
    global _initialized, _config, _clients

    if _initialized:
        return

    try:
        infra_path = Path(__file__).parent.parent.parent / "infra"

        # Get outputs
        result = subprocess.run(
            ["pulumi", "stack", "output", "--json"],
            cwd=infra_path,
            capture_output=True,
            text=True,
            check=False,
        )
        outputs = json.loads(result.stdout) if result.returncode == 0 else {}

        # Get config
        result = subprocess.run(
            ["pulumi", "config", "--show-secrets", "--json"],
            cwd=infra_path,
            capture_output=True,
            text=True,
            check=False,
        )
        secrets = json.loads(result.stdout) if result.returncode == 0 else {}

        # Build config
        _config.update(
            {
                "dynamo_table": outputs.get("dynamodb_table_name"),
                "pinecone_key": secrets.get(
                    "portfolio:PINECONE_API_KEY", {}
                ).get("value"),
                "pinecone_index": secrets.get(
                    "portfolio:PINECONE_INDEX_NAME", {}
                ).get("value"),
                "pinecone_host": secrets.get(
                    "portfolio:PINECONE_HOST", {}
                ).get("value"),
            }
        )

        # Initialize clients
        import boto3
        from pinecone import Pinecone

        if _config.get("dynamo_table"):
            _clients["dynamo"] = boto3.client("dynamodb")

        if _config.get("pinecone_key"):
            _clients["pinecone"] = Pinecone(api_key=_config["pinecone_key"])
            if _config.get("pinecone_index") and _config.get("pinecone_host"):
                _clients["pinecone_index"] = _clients["pinecone"].Index(
                    _config["pinecone_index"], host=_config["pinecone_host"]
                )
        _initialized = True

    except (
        subprocess.SubprocessError,
        json.JSONDecodeError,
        ImportError,
    ) as e:
        _config["error"] = str(e)
        _initialized = True


@mcp.tool()
def test_connection() -> dict:
    """Test connection to DynamoDB and Pinecone."""
    _lazy_init()

    results: Dict[str, Any] = {
        "configuration_loaded": "error" not in _config,
        "dynamo_connection": {"status": "not_configured"},
        "pinecone_connection": {"status": "not_configured"},
    }
    if "error" in _config:
        results["error"] = _config["error"]
        return results

    # Test DynamoDB
    if "dynamo" in _clients:
        try:
            response = _clients["dynamo"].describe_table(
                TableName=_config["dynamo_table"]
            )
            results["dynamo_connection"] = {
                "status": "connected",
                "table": _config["dynamo_table"],
                "item_count": response["Table"]["ItemCount"],
            }
        except ClientError as e:
            results["dynamo_connection"] = {"status": "error", "error": str(e)}

    # Test Pinecone
    if "pinecone_index" in _clients:
        try:
            stats = _clients["pinecone_index"].describe_index_stats()
            results["pinecone_connection"] = {
                "status": "connected",
                "index": _config.get("pinecone_index"),
                "vector_count": stats.total_vector_count,
            }
        except ApiException as e:
            results["pinecone_connection"] = {
                "status": "error",
                "error": str(e),
            }

    return results


@mcp.tool()
def validate_label(label: str) -> dict:
    """Check if a label is valid according to CORE_LABELS."""
    _lazy_init()
    from receipt_label.constants import CORE_LABELS

    if label in CORE_LABELS:
        return {
            "valid": True,
            "label": label,
            "description": CORE_LABELS[label],
        }
    else:
        return {
            "valid": False,
            "label": label,
            "message": f"Invalid label. Valid labels are: {', '.join(sorted(CORE_LABELS.keys()))}",
        }


@mcp.tool()
def get_receipt_labels(image_id: str, receipt_id: int) -> dict:
    """Get all labels for a receipt from DynamoDB."""
    _lazy_init()

    if "error" in _config:
        return {"success": False, "error": _config["error"]}
    if "dynamo" not in _clients:
        return {"success": False, "error": "DynamoDB client not configured"}

    try:
        from receipt_dynamo.entities import item_to_receipt_word_label

        response = _clients["dynamo"].query(
            TableName=_config["dynamo_table"],
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeNames={"#pk": "PK", "#sk": "SK"},
            ExpressionAttributeValues={
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk": {"S": f"RECEIPT#{receipt_id:05d}#"},
            },
            FilterExpression="attribute_exists(#type) AND #type = :type",
            ExpressionAttributeNames={"#type": "TYPE"},
            ExpressionAttributeValues={":type": {"S": "RECEIPT_WORD_LABEL"}},
        )

        labels = [
            item_to_receipt_word_label(item)
            for item in response.get("Items", [])
        ]
        return {
            "success": True,
            "labels": [dict(label) for label in labels],
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    _lazy_init()
    mcp.run()
