#!/usr/bin/env python3
"""
Simple test script to verify that the receipt_dynamo package can be imported correctly.
This helps diagnose issues with package paths in the IDE.
"""

import sys
import os

print(f"Python executable: {sys.executable}")
print(f"Python version: {sys.version}")
print(f"Current working directory: {os.getcwd()}")
print(f"Python path: {sys.path}")

try:
    import receipt_dynamo

    print(
        f"\nSuccessfully imported receipt_dynamo package from: {receipt_dynamo.__file__}"
    )

    # Try importing a few key modules
    from receipt_dynamo import DynamoClient

    print(f"Successfully imported DynamoClient")

    # Print package version
    print(
        f"Package version: {receipt_dynamo.__version__ if hasattr(receipt_dynamo, '__version__') else 'Not defined'}"
    )

    print("\nImport test successful!")
except ImportError as e:
    print(f"\nFailed to import receipt_dynamo: {e}")
    print("\nTroubleshooting suggestions:")
    print("1. Check that the package is installed or in your PYTHONPATH")
    print("2. Verify that your IDE is using the correct Python interpreter")
    print("3. Make sure the package structure is correct")
