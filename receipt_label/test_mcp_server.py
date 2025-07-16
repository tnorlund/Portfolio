"""Test script for the MCP validation server.

This script demonstrates how to use the MCP server programmatically.
"""

import asyncio
import json
from typing import Dict, Any, List

from mcp_validation_server import ReceiptValidationServer
from mcp.types import TextContent


async def test_server():
    """Test the MCP validation server with sample operations."""
    
    # Create server instance
    server = ReceiptValidationServer()
    
    print("=== MCP Validation Server Test ===\n")
    
    # Test 1: Initialize with stack
    print("1. Initializing with Pulumi stack...")
    result = await server.call_tool(
        "initialize_stack",
        {"stack_name": "tnorlund/portfolio/dev"}
    )
    print_result(result)
    
    # Test 2: Get stack info
    print("\n2. Getting stack info...")
    result = await server.call_tool("get_stack_info", {})
    print_result(result)
    
    # Test 3: Validate labels
    print("\n3. Validating labels...")
    result = await server.call_tool(
        "validate_labels",
        {
            "image_id": "550e8400-e29b-41d4-a716-446655440000",
            "receipt_id": 12345,
            "labels": [
                {"word_id": 1, "label": "MERCHANT_NAME", "text": "WALMART"},
                {"word_id": 2, "label": "DATE", "text": "01/15/2024"},
                {"word_id": 3, "label": "INVALID_LABEL", "text": "something"},
                {"word_id": 4, "label": "GRAND_TOTAL", "text": "$45.99"},
            ]
        }
    )
    print_result(result)
    
    # Test 4: Query similar labels
    print("\n4. Querying similar labels...")
    result = await server.call_tool(
        "query_similar_labels",
        {
            "text": "TOTAL",
            "merchant_name": "Walmart",
            "top_k": 3
        }
    )
    print_result(result)
    
    # Test 5: Persist validated labels
    print("\n5. Persisting validated labels...")
    result = await server.call_tool(
        "persist_validated_labels",
        {
            "image_id": "550e8400-e29b-41d4-a716-446655440000",
            "receipt_id": 12345,
            "validated_labels": [
                {
                    "word_id": 1,
                    "line_id": 1,
                    "label": "MERCHANT_NAME",
                    "reasoning": "Top text identified as merchant name",
                    "confidence": 0.95
                },
                {
                    "word_id": 2,
                    "line_id": 2,
                    "label": "DATE",
                    "reasoning": "Standard date format MM/DD/YYYY",
                    "confidence": 0.98
                },
                {
                    "word_id": 4,
                    "line_id": 10,
                    "label": "GRAND_TOTAL",
                    "reasoning": "Currency value at bottom of receipt",
                    "confidence": 0.90
                }
            ]
        }
    )
    print_result(result)
    
    # Test 6: Get receipt labels
    print("\n6. Getting receipt labels...")
    result = await server.call_tool(
        "get_receipt_labels",
        {
            "image_id": "550e8400-e29b-41d4-a716-446655440000",
            "receipt_id": 12345
        }
    )
    print_result(result)
    
    print("\n=== Test Complete ===")


def print_result(result: List[TextContent]):
    """Print the result from a tool call."""
    for content in result:
        print(content.text)


async def test_error_handling():
    """Test error handling scenarios."""
    
    server = ReceiptValidationServer()
    
    print("\n=== Testing Error Handling ===\n")
    
    # Test calling tool without initialization
    print("1. Calling tool without initialization...")
    result = await server.call_tool(
        "validate_labels",
        {"image_id": "test", "receipt_id": 1, "labels": []}
    )
    print_result(result)
    
    # Test invalid stack name
    print("\n2. Invalid stack name...")
    result = await server.call_tool(
        "initialize_stack",
        {"stack_name": "invalid/stack/name"}
    )
    print_result(result)
    
    # Test unknown tool
    print("\n3. Unknown tool...")
    result = await server.call_tool("unknown_tool", {})
    print_result(result)


async def test_workflow():
    """Test a complete validation workflow."""
    
    server = ReceiptValidationServer()
    
    print("\n=== Testing Complete Workflow ===\n")
    
    # Initialize
    await server.call_tool(
        "initialize_stack",
        {"stack_name": "tnorlund/portfolio/dev"}
    )
    
    # Sample receipt data
    receipt_words = [
        {"word_id": 1, "label": "WALMART", "text": "WALMART"},
        {"word_id": 2, "label": "STORE_NUMBER", "text": "#1234"},  # Invalid
        {"word_id": 3, "label": "DATE", "text": "01/15/2024"},
        {"word_id": 4, "label": "TIME", "text": "10:30"},
        {"word_id": 5, "label": "ITEM", "text": "MILK"},  # Invalid
        {"word_id": 6, "label": "PRICE", "text": "$3.99"},  # Invalid
        {"word_id": 7, "label": "TOTAL", "text": "TOTAL"},  # Invalid
        {"word_id": 8, "label": "CURRENCY", "text": "$45.99"},  # Invalid
    ]
    
    # Step 1: Validate
    print("Step 1: Validating labels...")
    validation_result = await server.call_tool(
        "validate_labels",
        {
            "image_id": "test-image-123",
            "receipt_id": 999,
            "labels": receipt_words
        }
    )
    print_result(validation_result)
    
    # Step 2: Find corrections for invalid labels
    print("\n\nStep 2: Finding corrections...")
    invalid_words = ["STORE_NUMBER", "ITEM", "PRICE", "TOTAL", "CURRENCY"]
    
    for word in invalid_words:
        print(f"\nFinding similar labels for '{word}':")
        result = await server.call_tool(
            "query_similar_labels",
            {"text": word, "top_k": 2}
        )
        print_result(result)
    
    # Step 3: Apply corrections and persist
    print("\n\nStep 3: Persisting corrected labels...")
    corrected_labels = [
        {"word_id": 1, "line_id": 1, "label": "MERCHANT_NAME", "confidence": 0.95},
        {"word_id": 3, "line_id": 2, "label": "DATE", "confidence": 0.98},
        {"word_id": 4, "line_id": 2, "label": "TIME", "confidence": 0.95},
        {"word_id": 5, "line_id": 5, "label": "PRODUCT_NAME", "confidence": 0.85},
        {"word_id": 6, "line_id": 5, "label": "LINE_TOTAL", "confidence": 0.80},
        {"word_id": 8, "line_id": 10, "label": "GRAND_TOTAL", "confidence": 0.90},
    ]
    
    result = await server.call_tool(
        "persist_validated_labels",
        {
            "image_id": "test-image-123",
            "receipt_id": 999,
            "validated_labels": corrected_labels
        }
    )
    print_result(result)
    
    # Step 4: Verify persistence
    print("\n\nStep 4: Verifying persisted labels...")
    result = await server.call_tool(
        "get_receipt_labels",
        {
            "image_id": "test-image-123",
            "receipt_id": 999
        }
    )
    print_result(result)


async def main():
    """Run all tests."""
    # Basic functionality tests
    await test_server()
    
    # Error handling tests
    await test_error_handling()
    
    # Complete workflow test
    await test_workflow()


if __name__ == "__main__":
    asyncio.run(main())