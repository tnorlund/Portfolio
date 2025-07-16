"""MCP server with SSE transport - can print debug output without issues."""

from mcp.server import FastMCP
from receipt_label.constants import CORE_LABELS
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# Create the MCP server
mcp = FastMCP("receipt-validation")

# Global state (SSE keeps server running)
config = {}
initialized = False
dynamo_client = None
pinecone_client = None
pinecone_index = None


def initialize():
    """Initialize clients with dev stack configuration."""
    global config, initialized, dynamo_client, pinecone_client, pinecone_index
    
    print("🚀 Initializing MCP Server with SSE transport...")
    
    # Get Pulumi config
    try:
        infra_path = Path(__file__).parent.parent / "infra"
        
        print("📁 Selecting Pulumi stack...")
        subprocess.run(
            ["pulumi", "stack", "select", "tnorlund/portfolio/dev"],
            cwd=infra_path,
            capture_output=True,
        )
        
        # Get outputs
        result = subprocess.run(
            ["pulumi", "stack", "output", "--json"],
            cwd=infra_path,
            capture_output=True,
            text=True,
        )
        outputs = json.loads(result.stdout) if result.returncode == 0 else {}
        
        # Get config
        result = subprocess.run(
            ["pulumi", "config", "--show-secrets", "--json"],
            cwd=infra_path,
            capture_output=True,
            text=True,
        )
        secrets = json.loads(result.stdout) if result.returncode == 0 else {}
        
        config = {
            "dynamo_table": outputs.get("dynamodb_table_name") or outputs.get("dynamoTableName"),
            "openai_key": secrets.get("portfolio:OPENAI_API_KEY", {}).get("value"),
            "pinecone_key": secrets.get("portfolio:PINECONE_API_KEY", {}).get("value"),
            "pinecone_index": secrets.get("portfolio:PINECONE_INDEX_NAME", {}).get("value"),
            "pinecone_host": secrets.get("portfolio:PINECONE_HOST", {}).get("value"),
        }
        
        print(f"✅ Found DynamoDB table: {config.get('dynamo_table')}")
        print(f"✅ Found Pinecone index: {config.get('pinecone_index')}")
        
        # Initialize clients
        import boto3
        from pinecone import Pinecone
        
        dynamo_client = boto3.client("dynamodb")
        pinecone_client = Pinecone(api_key=config["pinecone_key"])
        pinecone_index = pinecone_client.Index(
            config["pinecone_index"],
            host=config["pinecone_host"],
        )
        
        initialized = True
        print("✅ Server initialized successfully!")
        
    except Exception as e:
        print(f"❌ Initialization failed: {e}")
        initialized = False


# Initialize on startup (SSE can handle output)
initialize()


@mcp.tool()
def test_connection() -> str:
    """Test connection to DynamoDB and Pinecone."""
    results = []
    
    results.append(f"Configuration loaded: {'✓' if config else '✗'}")
    results.append(f"DynamoDB table: {config.get('dynamo_table', 'Not found')}")
    results.append(f"Pinecone index: {config.get('pinecone_index', 'Not found')}")
    
    if initialized:
        # Test DynamoDB
        try:
            response = dynamo_client.describe_table(TableName=config["dynamo_table"])
            results.append(f"DynamoDB connection: ✓ (Table has {response['Table']['ItemCount']} items)")
        except Exception as e:
            results.append(f"DynamoDB connection: ✗ ({str(e)[:50]}...)")
        
        # Test Pinecone
        try:
            stats = pinecone_index.describe_index_stats()
            results.append(f"Pinecone connection: ✓ (Index has {stats.total_vector_count} vectors)")
        except Exception as e:
            results.append(f"Pinecone connection: ✗ ({str(e)[:50]}...)")
    else:
        results.append("Services not initialized - check Pulumi configuration")
    
    return "\n".join(results)


@mcp.tool()
def validate_label(label: str) -> str:
    """Check if a label is valid according to CORE_LABELS."""
    if label in CORE_LABELS:
        return f"✓ '{label}' is valid\nDescription: {CORE_LABELS[label]}"
    else:
        valid_labels = ", ".join(sorted(CORE_LABELS.keys()))
        return f"✗ '{label}' is NOT valid\nValid labels: {valid_labels}"


@mcp.tool()
def get_receipt_labels(image_id: str, receipt_id: int) -> str:
    """Get all labels for a receipt from DynamoDB."""
    if not initialized:
        return "Error: Server not initialized"
    
    try:
        from receipt_dynamo.entities import item_to_receipt_word_label
        
        response = dynamo_client.query(
            TableName=config["dynamo_table"],
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            FilterExpression="attribute_exists(#type) AND #type = :type",
            ExpressionAttributeNames={"#type": "TYPE"},
            ExpressionAttributeValues={
                ":pk": f"IMAGE#{image_id}",
                ":sk": f"RECEIPT#{receipt_id:05d}#",
                ":type": "RECEIPT_WORD_LABEL",
            },
        )
        
        labels = []
        for item in response.get("Items", []):
            try:
                label_entity = item_to_receipt_word_label(item)
                labels.append(f"Word {label_entity.word_id}: {label_entity.label}")
            except:
                pass
        
        if labels:
            return f"Found {len(labels)} labels:\n" + "\n".join(labels)
        else:
            return "No labels found for this receipt"
        
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def save_label(
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    label: str,
    reasoning: str = "Manual validation"
) -> str:
    """Save a single label to DynamoDB."""
    if not initialized:
        return "Error: Server not initialized"
    
    try:
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
        
        dynamo_client.put_item(
            TableName=config["dynamo_table"],
            Item=label_entity.to_item(),
        )
        
        return f"✓ Saved label '{label}' for word {word_id}"
        
    except Exception as e:
        return f"Error saving label: {e}"


if __name__ == "__main__":
    # Run with SSE transport on port 8000
    print("🌐 Starting SSE server on http://localhost:8000")
    print("📡 Connect with: http://localhost:8000/sse")
    mcp.run(transport="sse")