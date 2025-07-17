"""MCP server with optimized client initialization and error handling."""

import logging
import os
import sys
import threading
from mcp.server import FastMCP
from typing import Dict, Any, Optional
from pathlib import Path

# Disable all logging to keep output clean for STDIO transport
logging.disable(logging.CRITICAL)

# Create the MCP server
mcp = FastMCP("receipt-validation")

# Thread-safe client manager instance
_client_manager: Optional["ClientManager"] = None
_manager_lock = threading.Lock()


def load_pulumi_config() -> dict:
    """Load configuration from Pulumi stack."""
    import json
    import subprocess
    import shutil

    # Find pulumi executable
    pulumi_path = shutil.which("pulumi")
    if not pulumi_path:
        # Try common locations
        for common_path in [
            "/Users/tnorlund/.pulumi/bin/pulumi",
            "/usr/local/bin/pulumi",
            "/opt/homebrew/bin/pulumi",
        ]:
            if os.path.exists(common_path):
                pulumi_path = common_path
                break
        else:
            raise RuntimeError(
                "Pulumi executable not found. Please ensure Pulumi is installed and in PATH."
            )

    try:
        # Calculate infra path more explicitly
        server_file = Path(__file__).resolve()  # Get absolute path

        # Expected structure: /Users/tnorlund/claude_b/Portfolio/receipt_label/mcp_isolated/server.py
        # Navigate up: mcp_isolated -> receipt_label -> Portfolio -> infra
        infra_path = server_file.parent.parent.parent / "infra"

        # Debug: print the paths to understand what's happening
        if not infra_path.exists():
            # Try alternative paths
            possible_paths = [
                server_file.parent.parent.parent / "infra",  # Portfolio/infra
                server_file.parent.parent.parent.parent
                / "infra",  # claude_b/infra
                Path(
                    "/Users/tnorlund/claude_b/Portfolio/infra"
                ),  # Absolute fallback
            ]

            for path in possible_paths:
                if path.exists():
                    infra_path = path
                    break
            else:
                raise RuntimeError(
                    f"Infra directory not found. Tried:\n"
                    + "\n".join([f"  - {p}" for p in possible_paths])
                    + f"\nServer file: {server_file}"
                )

        # Select stack
        result = subprocess.run(
            [pulumi_path, "stack", "select", "tnorlund/portfolio/dev"],
            cwd=infra_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to select Pulumi stack: {result.stderr}"
            )

        # Get outputs
        result = subprocess.run(
            [pulumi_path, "stack", "output", "--json"],
            cwd=infra_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to get Pulumi outputs: {result.stderr}"
            )

        outputs = json.loads(result.stdout) if result.stdout else {}

        # Get config
        result = subprocess.run(
            [pulumi_path, "config", "--show-secrets", "--json"],
            cwd=infra_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to get Pulumi config: {result.stderr}")

        secrets = json.loads(result.stdout) if result.stdout else {}

        # Build config and set env vars
        config = {
            "dynamo_table": outputs.get("dynamodb_table_name")
            or outputs.get("dynamoTableName"),
            "openai_key": secrets.get("portfolio:OPENAI_API_KEY", {}).get(
                "value"
            ),
            "pinecone_key": secrets.get("portfolio:PINECONE_API_KEY", {}).get(
                "value"
            ),
            "pinecone_index": secrets.get(
                "portfolio:PINECONE_INDEX_NAME", {}
            ).get("value"),
            "pinecone_host": secrets.get("portfolio:PINECONE_HOST", {}).get(
                "value"
            ),
        }

        # Validate we got the essential config
        if not config.get("dynamo_table"):
            raise RuntimeError("Failed to get DynamoDB table name from Pulumi")

        # Set environment variables for ClientManager
        for key, env_var in [
            ("openai_key", "OPENAI_API_KEY"),
            ("pinecone_key", "PINECONE_API_KEY"),
            ("pinecone_index", "PINECONE_INDEX_NAME"),
            ("pinecone_host", "PINECONE_HOST"),
            ("dynamo_table", "DYNAMODB_TABLE_NAME"),
        ]:
            if config.get(key):
                os.environ[env_var] = config[key]

        return config

    except subprocess.SubprocessError as e:
        raise RuntimeError(f"Subprocess error running Pulumi: {str(e)}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse Pulumi JSON output: {str(e)}")
    except Exception as e:
        raise RuntimeError(f"Failed to load Pulumi config: {str(e)}")


def get_client_manager():
    """Get or create the singleton client manager using the existing ClientManager."""
    global _client_manager

    if _client_manager is None:
        with _manager_lock:
            if _client_manager is None:  # Double-check pattern
                try:
                    # Load Pulumi config and set env vars
                    config = load_pulumi_config()

                    # Import and use the existing ClientManager
                    from receipt_label.utils.client_manager import (
                        ClientManager,
                        ClientConfig,
                    )

                    # Create config from environment (now populated by load_pulumi_config)
                    client_config = ClientConfig.from_env()
                    _client_manager = ClientManager(client_config)

                    # Store the loaded config for compatibility
                    _client_manager._pulumi_config = config

                except Exception as e:
                    raise RuntimeError(
                        f"Failed to initialize ClientManager: {str(e)}"
                    )

    return _client_manager


def get_health_status() -> dict:
    """Get health status of all services."""
    try:
        manager = get_client_manager()

        status = {
            "initialized": True,
            "config_loaded": bool(getattr(manager, "_pulumi_config", {})),
            "services": {},
        }

        # Test DynamoDB
        try:
            dynamo_client = manager.dynamo
            response = dynamo_client._client.describe_table(
                TableName=manager.config.dynamo_table
            )
            status["services"]["dynamo"] = {
                "status": "healthy",
                "table": manager.config.dynamo_table,
                "item_count": response["Table"]["ItemCount"],
            }
        except Exception as e:
            status["services"]["dynamo"] = {"status": "error", "error": str(e)}

        # Test Pinecone
        try:
            pinecone_index = manager.pinecone
            stats = pinecone_index.describe_index_stats()
            status["services"]["pinecone"] = {
                "status": "healthy",
                "index": manager.config.pinecone_index_name,
                "vector_count": stats.total_vector_count,
            }
        except Exception as e:
            status["services"]["pinecone"] = {
                "status": "error",
                "error": str(e),
            }

        return status

    except Exception as e:
        return {
            "initialized": False,
            "config_loaded": False,
            "services": {"error": str(e)},
        }


@mcp.tool()
def test_connection() -> dict:
    """Test connection to DynamoDB and Pinecone."""
    return get_health_status()


@mcp.tool()
def validate_label(label: str) -> dict:
    """Check if a label is valid according to CORE_LABELS."""
    # Lazy import to avoid initialization output
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
    manager = get_client_manager()

    try:
        from receipt_dynamo.entities import item_to_receipt_word_label

        response = manager.dynamo._client.query(
            TableName=manager.config.dynamo_table,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            FilterExpression="attribute_exists(#type) AND #type = :type",
            ExpressionAttributeNames={"#type": "TYPE"},
            ExpressionAttributeValues={
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk": {"S": f"RECEIPT#{receipt_id:05d}#"},
                ":type": {"S": "RECEIPT_WORD_LABEL"},
            },
        )

        labels = []
        for item in response.get("Items", []):
            try:
                label_entity = item_to_receipt_word_label(item)
                labels.append(
                    {
                        "word_id": label_entity.word_id,
                        "label": label_entity.label,
                        "line_id": label_entity.line_id,
                        "validation_status": label_entity.validation_status,
                    }
                )
            except:
                pass

        return {
            "success": True,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "label_count": len(labels),
            "labels": labels,
        }

    except Exception as e:
        return {"success": False, "error": str(e), "labels": []}


@mcp.tool()
def save_label(
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    label: str,
    reasoning: str = "Manual validation",
) -> dict:
    """Save a single label to DynamoDB."""
    manager = get_client_manager()

    try:
        from datetime import datetime
        from receipt_dynamo.entities import ReceiptWordLabel

        label_entity = ReceiptWordLabel(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            label=label,
            reasoning=reasoning,
            timestamp_added=datetime.now(),
            validation_status="VALID",
            label_proposed_by="mcp_validation",
        )

        manager.dynamo._client.put_item(
            TableName=manager.config.dynamo_table,
            Item=label_entity.to_item(),
        )

        return {
            "success": True,
            "saved": {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "word_id": word_id,
                "label": label,
                "reasoning": reasoning,
            },
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def list_receipts(
    limit: Optional[int] = None,
    last_evaluated_key: Optional[dict] = None,
) -> dict:
    """List receipts from DynamoDB with pagination support.

    Args:
        limit: Maximum number of receipts to return (default: all)
        last_evaluated_key: Pagination key from previous query

    Returns:
        Dictionary containing:
        - success: Boolean indicating if operation succeeded
        - receipts: List of receipt dictionaries
        - last_evaluated_key: Pagination key for next query (if more results exist)
        - count: Number of receipts returned
    """
    manager = get_client_manager()

    try:
        # Use the existing DynamoClient from ClientManager
        dynamo_client = manager.dynamo

        # Call list_receipts method
        receipts, next_key = dynamo_client.list_receipts(
            limit=limit, last_evaluated_key=last_evaluated_key
        )

        # Convert Receipt objects to dictionaries
        receipt_dicts = []
        for receipt in receipts:
            receipt_dict = dict(receipt)
            # Add optional fields if they exist
            if hasattr(receipt, "merchant_name") and receipt.merchant_name:
                receipt_dict["merchant_name"] = receipt.merchant_name
            if hasattr(receipt, "total_amount") and receipt.total_amount:
                receipt_dict["total_amount"] = str(receipt.total_amount)
            if hasattr(receipt, "date") and receipt.date:
                receipt_dict["date"] = receipt.date

            receipt_dicts.append(receipt_dict)

        result = {
            "success": True,
            "receipts": receipt_dicts,
            "count": len(receipt_dicts),
        }

        # Add pagination key if there are more results
        if next_key:
            result["last_evaluated_key"] = next_key
            result["has_more"] = True
        else:
            result["has_more"] = False

        return result

    except Exception as e:
        return {"success": False, "error": str(e), "receipts": [], "count": 0}


if __name__ == "__main__":
    # Initialize clients at startup instead of on first request
    get_client_manager()
    mcp.run()
