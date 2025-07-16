#!/usr/bin/env python3
"""Test script to verify Pulumi access for MCP server."""

from mcp_validation_server_simple import test_pulumi_access

if __name__ == "__main__":
    print("Testing Pulumi access for dev stack...\n")
    
    results = test_pulumi_access("dev")
    
    print("\n=== Test Results ===")
    print(f"Success: {results['success']}")
    print(f"Stack found: {results['stack_found']}")
    print(f"Outputs found: {len(results['outputs'])}")
    print(f"Secrets found: {len(results['secrets'])}")
    
    if results['errors']:
        print("\nErrors:")
        for error in results['errors']:
            print(f"  - {error}")
    
    if results['success']:
        print("\n✅ All tests passed! The MCP server should work correctly.")
        print("\nConfiguration found:")
        if 'dynamoTableName' in results['outputs']:
            print(f"  - DynamoDB Table: {results['outputs']['dynamoTableName']}")
        for key in results['secrets']:
            if 'API_KEY' in key:
                print(f"  - {key}: ***configured***")
            else:
                print(f"  - {key}: {results['secrets'][key].get('value', 'N/A')}")
    else:
        print("\n❌ Tests failed. Please check the errors above.")