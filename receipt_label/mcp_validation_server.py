"""MCP server for receipt validation operations.

This server provides tools for validating receipt labels using DynamoDB and Pinecone,
automatically fetching configuration from the specified Pulumi stack.
"""

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# Import receipt_label modules
from receipt_label.utils.client_manager import ClientManager
from receipt_label.constants import CORE_LABELS
from receipt_dynamo.entities import ReceiptWordLabel
from receipt_dynamo.constants import ValidationStatus


class ReceiptValidationServer:
    """MCP server for receipt validation operations."""
    
    def __init__(self):
        self.server = Server("receipt-validation")
        self.client_manager: Optional[ClientManager] = None
        self.pulumi_config: Dict[str, Any] = {}
        self.stack_name: Optional[str] = None
        
        # Register server handlers
        self.server.list_tools = self.list_tools
        self.server.call_tool = self.call_tool
    
    async def get_pulumi_config(self, stack_name: str) -> Dict[str, Any]:
        """Fetch configuration from Pulumi stack.
        
        Args:
            stack_name: Pulumi stack name (e.g., 'tnorlund/portfolio/dev')
            
        Returns:
            Dict containing configuration values
        """
        try:
            # Change to infra directory
            infra_path = Path(__file__).parent.parent / "infra"
            
            # Select the stack
            result = subprocess.run(
                ["pulumi", "stack", "select", stack_name],
                cwd=infra_path,
                capture_output=True,
                text=True
            )
            
            if result.returncode != 0:
                raise RuntimeError(f"Failed to select stack: {result.stderr}")
            
            # Get all configuration
            result = subprocess.run(
                ["pulumi", "config", "--json"],
                cwd=infra_path,
                capture_output=True,
                text=True
            )
            
            if result.returncode != 0:
                raise RuntimeError(f"Failed to get config: {result.stderr}")
            
            config = json.loads(result.stdout)
            
            # Get stack outputs
            result = subprocess.run(
                ["pulumi", "stack", "output", "--json"],
                cwd=infra_path,
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                outputs = json.loads(result.stdout)
                config["outputs"] = outputs
            
            return config
            
        except Exception as e:
            raise RuntimeError(f"Failed to fetch Pulumi config: {e}")
    
    async def initialize_clients(self, stack_name: str):
        """Initialize DynamoDB and Pinecone clients from Pulumi stack config.
        
        Args:
            stack_name: Pulumi stack name
        """
        if self.client_manager and self.stack_name == stack_name:
            return  # Already initialized for this stack
        
        # Get Pulumi configuration
        self.pulumi_config = await self.get_pulumi_config(stack_name)
        self.stack_name = stack_name
        
        # Extract necessary values
        config_values = {}
        
        # Get from Pulumi config
        if "portfolio:OPENAI_API_KEY" in self.pulumi_config:
            config_values["OPENAI_API_KEY"] = self.pulumi_config["portfolio:OPENAI_API_KEY"]["value"]
        
        if "portfolio:PINECONE_API_KEY" in self.pulumi_config:
            config_values["PINECONE_API_KEY"] = self.pulumi_config["portfolio:PINECONE_API_KEY"]["value"]
        
        if "portfolio:PINECONE_INDEX_NAME" in self.pulumi_config:
            config_values["PINECONE_INDEX_NAME"] = self.pulumi_config["portfolio:PINECONE_INDEX_NAME"]["value"]
        
        if "portfolio:PINECONE_HOST" in self.pulumi_config:
            config_values["PINECONE_HOST"] = self.pulumi_config["portfolio:PINECONE_HOST"]["value"]
        
        # Get DynamoDB table name from outputs
        if "outputs" in self.pulumi_config and "dynamoTableName" in self.pulumi_config["outputs"]:
            config_values["DYNAMO_TABLE_NAME"] = self.pulumi_config["outputs"]["dynamoTableName"]
        
        # Initialize client manager with config
        self.client_manager = ClientManager(
            openai_api_key=config_values.get("OPENAI_API_KEY"),
            pinecone_api_key=config_values.get("PINECONE_API_KEY"),
            pinecone_index_name=config_values.get("PINECONE_INDEX_NAME"),
            pinecone_host=config_values.get("PINECONE_HOST"),
            dynamo_table_name=config_values.get("DYNAMO_TABLE_NAME"),
        )
    
    async def list_tools(self) -> List[Tool]:
        """List available tools."""
        return [
            Tool(
                name="initialize_stack",
                description="Initialize the server with a Pulumi stack configuration",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "stack_name": {
                            "type": "string",
                            "description": "Pulumi stack name (e.g., 'tnorlund/portfolio/dev')"
                        }
                    },
                    "required": ["stack_name"]
                }
            ),
            Tool(
                name="validate_labels",
                description="Validate receipt labels against CORE_LABELS schema",
                inputSchema={
                    "type": "object", 
                    "properties": {
                        "image_id": {"type": "string"},
                        "receipt_id": {"type": "integer"},
                        "labels": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "word_id": {"type": "integer"},
                                    "label": {"type": "string"},
                                    "text": {"type": "string"}
                                }
                            }
                        }
                    },
                    "required": ["image_id", "receipt_id", "labels"]
                }
            ),
            Tool(
                name="query_similar_labels",
                description="Query Pinecone for similar valid labels",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "merchant_name": {"type": "string"},
                        "top_k": {"type": "integer", "default": 5}
                    },
                    "required": ["text"]
                }
            ),
            Tool(
                name="persist_validated_labels",
                description="Persist validated labels to DynamoDB",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "image_id": {"type": "string"},
                        "receipt_id": {"type": "integer"},
                        "validated_labels": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "word_id": {"type": "integer"},
                                    "line_id": {"type": "integer"},
                                    "label": {"type": "string"},
                                    "reasoning": {"type": "string"},
                                    "confidence": {"type": "number"}
                                }
                            }
                        }
                    },
                    "required": ["image_id", "receipt_id", "validated_labels"]
                }
            ),
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
                name="get_stack_info",
                description="Get current Pulumi stack configuration info",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            )
        ]
    
    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> List[TextContent]:
        """Execute a tool and return results."""
        
        if name == "initialize_stack":
            try:
                stack_name = arguments["stack_name"]
                await self.initialize_clients(stack_name)
                
                return [TextContent(
                    type="text",
                    text=f"Successfully initialized with stack '{stack_name}'\n"
                         f"DynamoDB Table: {self.pulumi_config.get('outputs', {}).get('dynamoTableName', 'N/A')}\n"
                         f"Pinecone Index: {self.pulumi_config.get('portfolio:PINECONE_INDEX_NAME', {}).get('value', 'N/A')}"
                )]
            except Exception as e:
                return [TextContent(type="text", text=f"Error initializing stack: {e}")]
        
        # Check if clients are initialized for other tools
        if not self.client_manager:
            return [TextContent(
                type="text", 
                text="Error: Please initialize with a Pulumi stack first using 'initialize_stack'"
            )]
        
        if name == "validate_labels":
            return await self._validate_labels(arguments)
        
        elif name == "query_similar_labels":
            return await self._query_similar_labels(arguments)
        
        elif name == "persist_validated_labels":
            return await self._persist_validated_labels(arguments)
        
        elif name == "get_receipt_labels":
            return await self._get_receipt_labels(arguments)
        
        elif name == "get_stack_info":
            return await self._get_stack_info()
        
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    
    async def _validate_labels(self, args: Dict[str, Any]) -> List[TextContent]:
        """Validate labels against CORE_LABELS schema."""
        labels = args["labels"]
        results = []
        
        for label_info in labels:
            word_id = label_info["word_id"]
            label = label_info["label"]
            text = label_info.get("text", "")
            
            if label not in CORE_LABELS:
                results.append(f"Word {word_id} ('{text}'): Invalid label '{label}' - not in CORE_LABELS")
            else:
                results.append(f"Word {word_id} ('{text}'): Valid label '{label}'")
        
        return [TextContent(type="text", text="\n".join(results))]
    
    async def _query_similar_labels(self, args: Dict[str, Any]) -> List[TextContent]:
        """Query Pinecone for similar valid labels."""
        text = args["text"]
        merchant_name = args.get("merchant_name")
        top_k = args.get("top_k", 5)
        
        try:
            # Build query filter
            filter_criteria = {
                "embedding_type": "word",
                "valid_labels": {"$exists": True, "$ne": []},
            }
            
            if merchant_name:
                filter_criteria["merchant_name"] = merchant_name
            
            # Query Pinecone
            results = self.client_manager.pinecone.query(
                vector=[0.0] * 1536,  # Dummy vector for text search
                top_k=top_k,
                include_metadata=True,
                namespace="words",
                filter=filter_criteria
            )
            
            suggestions = []
            for match in results.matches:
                metadata = match.metadata or {}
                valid_labels = metadata.get("valid_labels", [])
                matched_text = metadata.get("text", "")
                score = match.score if hasattr(match, 'score') else 0.0
                
                suggestions.append(
                    f"'{matched_text}' → {valid_labels} (score: {score:.2f})"
                )
            
            return [TextContent(
                type="text",
                text=f"Similar labels for '{text}':\n" + "\n".join(suggestions)
            )]
            
        except Exception as e:
            return [TextContent(type="text", text=f"Error querying Pinecone: {e}")]
    
    async def _persist_validated_labels(self, args: Dict[str, Any]) -> List[TextContent]:
        """Persist validated labels to DynamoDB."""
        image_id = args["image_id"]
        receipt_id = args["receipt_id"]
        validated_labels = args["validated_labels"]
        
        try:
            success_count = 0
            errors = []
            
            for label_info in validated_labels:
                try:
                    label_entity = ReceiptWordLabel(
                        image_id=image_id,
                        receipt_id=receipt_id,
                        line_id=label_info["line_id"],
                        word_id=label_info["word_id"],
                        label=label_info["label"],
                        reasoning=label_info.get("reasoning", "MCP validation"),
                        timestamp_added=datetime.now(),
                        validation_status=ValidationStatus.VALID.value,
                        label_proposed_by="mcp_validation_server"
                    )
                    
                    # Write to DynamoDB
                    self.client_manager.dynamo.put_item(
                        TableName=self.client_manager.dynamo_table_name,
                        Item=label_entity.to_item()
                    )
                    success_count += 1
                    
                except Exception as e:
                    errors.append(f"Word {label_info['word_id']}: {e}")
            
            result = f"Persisted {success_count}/{len(validated_labels)} labels"
            if errors:
                result += f"\nErrors:\n" + "\n".join(errors)
            
            return [TextContent(type="text", text=result)]
            
        except Exception as e:
            return [TextContent(type="text", text=f"Error persisting labels: {e}")]
    
    async def _get_receipt_labels(self, args: Dict[str, Any]) -> List[TextContent]:
        """Get all labels for a receipt from DynamoDB."""
        image_id = args["image_id"]
        receipt_id = args["receipt_id"]
        
        try:
            # Query for all labels for this receipt
            response = self.client_manager.dynamo.query(
                TableName=self.client_manager.dynamo_table_name,
                KeyConditionExpression="PK = :pk AND begins_with(SK, :sk_prefix)",
                ExpressionAttributeValues={
                    ":pk": {"S": f"IMAGE#{image_id}"},
                    ":sk_prefix": {"S": f"RECEIPT#{receipt_id:05d}"}
                },
                FilterExpression="TYPE = :type",
                ExpressionAttributeValues={
                    ":type": {"S": "RECEIPT_WORD_LABEL"}
                }
            )
            
            labels = []
            for item in response.get("Items", []):
                # Parse label from SK
                sk_parts = item["SK"]["S"].split("#")
                if len(sk_parts) >= 8:  # Has LABEL# part
                    labels.append({
                        "word_id": int(sk_parts[5]),
                        "label": sk_parts[7],
                        "validation_status": item.get("validation_status", {}).get("S", "NONE"),
                        "reasoning": item.get("reasoning", {}).get("S", "")
                    })
            
            result = f"Found {len(labels)} labels for receipt {receipt_id}:\n"
            for label in labels:
                result += f"  Word {label['word_id']}: {label['label']} ({label['validation_status']})\n"
            
            return [TextContent(type="text", text=result)]
            
        except Exception as e:
            return [TextContent(type="text", text=f"Error getting labels: {e}")]
    
    async def _get_stack_info(self) -> List[TextContent]:
        """Get current stack configuration info."""
        if not self.stack_name:
            return [TextContent(type="text", text="No stack initialized")]
        
        info = [
            f"Current Stack: {self.stack_name}",
            f"DynamoDB Table: {self.pulumi_config.get('outputs', {}).get('dynamoTableName', 'N/A')}",
            f"Pinecone Index: {self.pulumi_config.get('portfolio:PINECONE_INDEX_NAME', {}).get('value', 'N/A')}",
            f"Pinecone Host: {self.pulumi_config.get('portfolio:PINECONE_HOST', {}).get('value', 'N/A')}",
        ]
        
        return [TextContent(type="text", text="\n".join(info))]


async def main():
    """Run the MCP server."""
    server = ReceiptValidationServer()
    
    async with stdio_server() as (read_stream, write_stream):
        await server.server.run(read_stream, write_stream)


if __name__ == "__main__":
    # Add missing import
    from datetime import datetime
    
    asyncio.run(main())