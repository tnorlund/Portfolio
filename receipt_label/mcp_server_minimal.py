"""Minimal MCP server for receipt validation - no output during initialization."""

from mcp.server import FastMCP

# Create the MCP server
mcp = FastMCP("receipt-validation")

# Lazy imports and initialization
_initialized = False
_config = None
_clients = None


def _lazy_init():
    """Lazy initialization of all dependencies."""
    global _initialized, _config, _clients
    
    if _initialized:
        return _config, _clients
    
    import json
    import os
    import subprocess
    from pathlib import Path
    
    # Get Pulumi config silently
    try:
        infra_path = Path(__file__).parent.parent / "infra"
        
        # Select stack
        subprocess.run(
            ["pulumi", "stack", "select", "tnorlund/portfolio/dev"],
            cwd=infra_path,
            capture_output=True,
            text=True,
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
        
        # Build config
        _config = {
            "dynamo_table": outputs.get("dynamodb_table_name") or outputs.get("dynamoTableName"),
            "openai_key": secrets.get("portfolio:OPENAI_API_KEY", {}).get("value"),
            "pinecone_key": secrets.get("portfolio:PINECONE_API_KEY", {}).get("value"),
            "pinecone_index": secrets.get("portfolio:PINECONE_INDEX_NAME", {}).get("value"),
            "pinecone_host": secrets.get("portfolio:PINECONE_HOST", {}).get("value"),
        }
        
        # Set env vars
        for key, env_var in [
            ("openai_key", "OPENAI_API_KEY"),
            ("pinecone_key", "PINECONE_API_KEY"),
            ("pinecone_index", "PINECONE_INDEX_NAME"),
            ("pinecone_host", "PINECONE_HOST"),
            ("dynamo_table", "DYNAMO_TABLE_NAME"),
        ]:
            if _config.get(key):
                os.environ[env_var] = _config[key]
        
        # Initialize clients
        import boto3
        from pinecone import Pinecone
        
        _clients = {
            "dynamo": boto3.client("dynamodb"),
            "pinecone": Pinecone(api_key=_config["pinecone_key"]),
        }
        _clients["pinecone_index"] = _clients["pinecone"].Index(
            _config["pinecone_index"],
            host=_config["pinecone_host"],
        )
        
        _initialized = True
        
    except Exception:
        _config = {}
        _clients = {}
    
    return _config, _clients


@mcp.tool()
def test_connection() -> str:
    """Test connection to DynamoDB and Pinecone."""
    config, clients = _lazy_init()
    
    results = []
    results.append(f"Configuration loaded: {'✓' if config else '✗'}")
    results.append(f"DynamoDB table: {config.get('dynamo_table', 'Not found')}")
    results.append(f"Pinecone index: {config.get('pinecone_index', 'Not found')}")
    
    if clients and clients.get("dynamo") and clients.get("pinecone_index"):
        # Test DynamoDB
        try:
            response = clients["dynamo"].describe_table(TableName=config["dynamo_table"])
            results.append(f"DynamoDB connection: ✓ (Table has {response['Table']['ItemCount']} items)")
        except Exception as e:
            results.append(f"DynamoDB connection: ✗ ({str(e)[:50]}...)")
        
        # Test Pinecone
        try:
            stats = clients["pinecone_index"].describe_index_stats()
            results.append(f"Pinecone connection: ✓ (Index has {stats.total_vector_count} vectors)")
        except Exception as e:
            results.append(f"Pinecone connection: ✗ ({str(e)[:50]}...)")
    else:
        results.append("Services not initialized - check Pulumi configuration")
    
    return "\n".join(results)


@mcp.tool()
def validate_label(label: str) -> str:
    """Check if a label is valid according to CORE_LABELS."""
    # Lazy import to avoid initialization output
    from receipt_label.constants import CORE_LABELS
    
    if label in CORE_LABELS:
        return f"✓ '{label}' is valid\nDescription: {CORE_LABELS[label]}"
    else:
        valid_labels = ", ".join(sorted(CORE_LABELS.keys()))
        return f"✗ '{label}' is NOT valid\nValid labels: {valid_labels}"


@mcp.tool()
def get_receipt_labels(image_id: str, receipt_id: int) -> str:
    """Get all labels for a receipt from DynamoDB."""
    config, clients = _lazy_init()
    
    if not clients or not clients.get("dynamo"):
        return "Error: Server not initialized"
    
    try:
        from receipt_dynamo.entities import item_to_receipt_word_label
        
        response = clients["dynamo"].query(
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
    config, clients = _lazy_init()
    
    if not clients or not clients.get("dynamo"):
        return "Error: Server not initialized"
    
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
        
        clients["dynamo"].put_item(
            TableName=config["dynamo_table"],
            Item=label_entity.to_item(),
        )
        
        return f"✓ Saved label '{label}' for word {word_id}"
        
    except Exception as e:
        return f"Error saving label: {e}"


if __name__ == "__main__":
    mcp.run()