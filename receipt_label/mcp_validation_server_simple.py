"""Simple MCP server for receipt validation that auto-initializes with dev stack."""

import asyncio
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# Import receipt modules
from receipt_label.constants import CORE_LABELS


def test_pulumi_access(stack_name: str = "dev") -> Dict[str, Any]:
    """Test if we can access Pulumi stack and get configuration.
    
    Args:
        stack_name: Stack name (just "dev" or "prod", not full name)
        
    Returns:
        Dict with test results and any configuration found
    """
    results = {
        "success": False,
        "stack_found": False,
        "outputs": {},
        "secrets": {},
        "errors": []
    }
    
    full_stack_name = f"tnorlund/portfolio/{stack_name}"
    
    # Find infra directory
    current_path = Path(__file__).parent.parent
    infra_path = current_path / "infra"
    
    if not infra_path.exists():
        results["errors"].append(f"Infra directory not found at {infra_path}")
        return results
    
    print(f"Testing Pulumi access for stack: {full_stack_name}")
    print(f"Working directory: {infra_path}")
    
    # Test 1: Check if stack exists
    try:
        result = subprocess.run(
            ["pulumi", "stack", "select", full_stack_name],
            cwd=infra_path,
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            results["stack_found"] = True
            print(f"✓ Stack '{full_stack_name}' found")
        else:
            results["errors"].append(f"Stack select failed: {result.stderr}")
            print(f"✗ Stack select failed: {result.stderr}")
            return results
            
    except Exception as e:
        results["errors"].append(f"Failed to run pulumi command: {e}")
        print(f"✗ Failed to run pulumi command: {e}")
        return results
    
    # Test 2: Get stack outputs
    try:
        result = subprocess.run(
            ["pulumi", "stack", "output", "--json"],
            cwd=infra_path,
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            outputs = json.loads(result.stdout)
            results["outputs"] = outputs
            print(f"✓ Got {len(outputs)} stack outputs")
            
            # Show important outputs
            if "dynamoTableName" in outputs:
                print(f"  - DynamoDB Table: {outputs['dynamoTableName']}")
        else:
            results["errors"].append(f"Failed to get outputs: {result.stderr}")
            print(f"✗ Failed to get outputs: {result.stderr}")
            
    except Exception as e:
        results["errors"].append(f"Failed to get outputs: {e}")
        print(f"✗ Failed to get outputs: {e}")
    
    # Test 3: Get configuration (including secrets)
    try:
        result = subprocess.run(
            ["pulumi", "config", "--show-secrets", "--json"],
            cwd=infra_path,
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            config = json.loads(result.stdout)
            
            # Extract the values we need
            needed_keys = [
                "portfolio:OPENAI_API_KEY",
                "portfolio:PINECONE_API_KEY", 
                "portfolio:PINECONE_INDEX_NAME",
                "portfolio:PINECONE_HOST"
            ]
            
            found_keys = []
            for key in needed_keys:
                if key in config:
                    found_keys.append(key)
                    # Don't print secret values
                    if "API_KEY" in key:
                        print(f"  - {key}: ***hidden***")
                    else:
                        print(f"  - {key}: {config[key].get('value', 'N/A')}")
            
            results["secrets"] = {k: config[k] for k in found_keys}
            print(f"✓ Found {len(found_keys)}/{len(needed_keys)} required config values")
            
            missing_keys = set(needed_keys) - set(found_keys)
            if missing_keys:
                results["errors"].append(f"Missing config: {', '.join(missing_keys)}")
                
        else:
            results["errors"].append(f"Failed to get config: {result.stderr}")
            print(f"✗ Failed to get config: {result.stderr}")
            
    except Exception as e:
        results["errors"].append(f"Failed to get config: {e}")
        print(f"✗ Failed to get config: {e}")
    
    # Determine overall success
    results["success"] = (
        results["stack_found"] and 
        len(results["outputs"]) > 0 and
        len(results["secrets"]) >= 4
    )
    
    print(f"\nOverall result: {'SUCCESS' if results['success'] else 'FAILED'}")
    
    return results


class SimpleValidationServer:
    """Simple MCP server that auto-initializes with dev stack."""
    
    def __init__(self):
        self.server = Server("receipt-validation-simple")
        self.dynamo_client = None
        self.pinecone_client = None
        self.config = {}
        self.initialized = False
        
        # Register handlers
        self.server.list_tools = self.list_tools
        self.server.call_tool = self.call_tool
        
        # Try to initialize on startup
        print("\n=== Initializing MCP Validation Server ===")
        self._initialize()
    
    def _initialize(self):
        """Initialize clients with dev stack configuration."""
        test_results = test_pulumi_access("dev")
        
        if not test_results["success"]:
            print("\n⚠️  Failed to initialize from Pulumi stack")
            print("Errors:", test_results["errors"])
            return
        
        # Store configuration
        self.config = {
            "dynamo_table": test_results["outputs"].get("dynamoTableName"),
            "openai_key": test_results["secrets"].get("portfolio:OPENAI_API_KEY", {}).get("value"),
            "pinecone_key": test_results["secrets"].get("portfolio:PINECONE_API_KEY", {}).get("value"),
            "pinecone_index": test_results["secrets"].get("portfolio:PINECONE_INDEX_NAME", {}).get("value"),
            "pinecone_host": test_results["secrets"].get("portfolio:PINECONE_HOST", {}).get("value"),
        }
        
        # Set environment variables
        if self.config["openai_key"]:
            os.environ["OPENAI_API_KEY"] = self.config["openai_key"]
        if self.config["pinecone_key"]:
            os.environ["PINECONE_API_KEY"] = self.config["pinecone_key"]
        if self.config["pinecone_index"]:
            os.environ["PINECONE_INDEX_NAME"] = self.config["pinecone_index"]
        if self.config["pinecone_host"]:
            os.environ["PINECONE_HOST"] = self.config["pinecone_host"]
        if self.config["dynamo_table"]:
            os.environ["DYNAMO_TABLE_NAME"] = self.config["dynamo_table"]
        
        # Try to initialize clients
        try:
            import boto3
            from pinecone import Pinecone
            
            # Initialize boto3 DynamoDB client
            self.dynamo_client = boto3.client('dynamodb')
            
            # Initialize Pinecone
            self.pinecone_client = Pinecone(api_key=self.config["pinecone_key"])
            self.pinecone_index = self.pinecone_client.Index(
                self.config["pinecone_index"],
                host=self.config["pinecone_host"]
            )
            
            self.initialized = True
            print("\n✅ Successfully initialized clients from Pulumi dev stack")
            print(f"   - DynamoDB table: {self.config['dynamo_table']}")
            print(f"   - Pinecone index: {self.config['pinecone_index']}")
        except Exception as e:
            print(f"\n❌ Failed to initialize clients: {e}")
            self.initialized = False
    
    async def list_tools(self) -> List[Tool]:
        """List available tools."""
        tools = [
            Tool(
                name="test_connection",
                description="Test connection to DynamoDB and Pinecone",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="validate_label",
                description="Check if a label is valid according to CORE_LABELS",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Label to validate"}
                    },
                    "required": ["label"]
                }
            ),
        ]
        
        # Only add these tools if initialized
        if self.initialized:
            tools.extend([
                Tool(
                    name="get_receipt_labels",
                    description="Get all labels for a receipt from DynamoDB",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "image_id": {"type": "string"},
                            "receipt_id": {"type": "integer"}
                        },
                        "required": ["image_id", "receipt_id"]
                    }
                ),
                Tool(
                    name="save_label",
                    description="Save a single label to DynamoDB",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "image_id": {"type": "string"},
                            "receipt_id": {"type": "integer"},
                            "line_id": {"type": "integer"},
                            "word_id": {"type": "integer"},
                            "label": {"type": "string"},
                            "reasoning": {"type": "string"}
                        },
                        "required": ["image_id", "receipt_id", "line_id", "word_id", "label"]
                    }
                ),
            ])
        
        return tools
    
    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> List[TextContent]:
        """Execute a tool."""
        
        if name == "test_connection":
            return await self._test_connection()
        
        elif name == "validate_label":
            label = arguments["label"]
            if label in CORE_LABELS:
                return [TextContent(
                    type="text",
                    text=f"✓ '{label}' is valid\nDescription: {CORE_LABELS[label]}"
                )]
            else:
                valid_labels = ", ".join(sorted(CORE_LABELS.keys()))
                return [TextContent(
                    type="text",
                    text=f"✗ '{label}' is NOT valid\nValid labels: {valid_labels}"
                )]
        
        elif name == "get_receipt_labels":
            if not self.initialized:
                return [TextContent(type="text", text="Error: Server not initialized")]
            return await self._get_receipt_labels(arguments)
        
        elif name == "save_label":
            if not self.initialized:
                return [TextContent(type="text", text="Error: Server not initialized")]
            return await self._save_label(arguments)
        
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    
    async def _test_connection(self) -> List[TextContent]:
        """Test connection to services."""
        results = []
        
        results.append(f"Configuration loaded: {'✓' if self.config else '✗'}")
        results.append(f"DynamoDB table: {self.config.get('dynamo_table', 'Not found')}")
        results.append(f"Pinecone index: {self.config.get('pinecone_index', 'Not found')}")
        
        if self.initialized:
            # Test DynamoDB
            try:
                response = self.dynamo_client.describe_table(
                    TableName=self.config["dynamo_table"]
                )
                results.append(f"DynamoDB connection: ✓ (Table has {response['Table']['ItemCount']} items)")
            except Exception as e:
                results.append(f"DynamoDB connection: ✗ ({str(e)[:50]}...)")
            
            # Test Pinecone
            try:
                stats = self.pinecone_index.describe_index_stats()
                results.append(f"Pinecone connection: ✓ (Index has {stats.total_vector_count} vectors)")
            except Exception as e:
                results.append(f"Pinecone connection: ✗ ({str(e)[:50]}...)")
        else:
            results.append("Services not initialized - check Pulumi configuration")
        
        return [TextContent(type="text", text="\n".join(results))]
    
    async def _get_receipt_labels(self, args: Dict[str, Any]) -> List[TextContent]:
        """Get labels for a receipt."""
        try:
            from receipt_dynamo.entities import item_to_receipt_word_label
            
            response = self.dynamo_client.query(
                TableName=self.config["dynamo_table"],
                KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                FilterExpression="attribute_exists(#type) AND #type = :type",
                ExpressionAttributeNames={"#type": "TYPE"},
                ExpressionAttributeValues={
                    ":pk": f"IMAGE#{args['image_id']}",
                    ":sk": f"RECEIPT#{args['receipt_id']:05d}#",
                    ":type": "RECEIPT_WORD_LABEL"
                }
            )
            
            labels = []
            for item in response.get("Items", []):
                try:
                    label_entity = item_to_receipt_word_label(item)
                    labels.append(f"Word {label_entity.word_id}: {label_entity.label}")
                except:
                    pass
            
            if labels:
                return [TextContent(type="text", text=f"Found {len(labels)} labels:\n" + "\n".join(labels))]
            else:
                return [TextContent(type="text", text="No labels found for this receipt")]
                
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    
    async def _save_label(self, args: Dict[str, Any]) -> List[TextContent]:
        """Save a single label."""
        try:
            from receipt_dynamo.entities import ReceiptWordLabel
            
            label_entity = ReceiptWordLabel(
                image_id=args["image_id"],
                receipt_id=args["receipt_id"],
                line_id=args["line_id"],
                word_id=args["word_id"],
                label=args["label"],
                reasoning=args.get("reasoning", "Manual validation"),
                timestamp_added=datetime.now(),
                validation_status="VALID",
                label_proposed_by="mcp_validation"
            )
            
            self.dynamo_client.put_item(
                TableName=self.config["dynamo_table"],
                Item=label_entity.to_item()
            )
            
            return [TextContent(type="text", text=f"✓ Saved label '{args['label']}' for word {args['word_id']}")]
            
        except Exception as e:
            return [TextContent(type="text", text=f"Error saving label: {e}")]


async def main():
    """Run the simple MCP server."""
    server = SimpleValidationServer()
    
    async with stdio_server() as (read_stream, write_stream):
        init_options = {
            "server_name": "receipt-validation-simple",
            "server_version": "0.1.0"
        }
        await server.server.run(read_stream, write_stream, init_options)


if __name__ == "__main__":
    # Test Pulumi access directly
    print("Testing Pulumi access...")
    test_results = test_pulumi_access("dev")
    
    if test_results["success"]:
        print("\n✅ Pulumi test passed! Starting server...")
        asyncio.run(main())
    else:
        print("\n❌ Pulumi test failed. Please check:")
        print("1. You're in the right directory")
        print("2. Pulumi CLI is installed") 
        print("3. You're logged in to Pulumi")
        print("4. The stack 'tnorlund/portfolio/dev' exists")