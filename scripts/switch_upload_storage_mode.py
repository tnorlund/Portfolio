#!/usr/bin/env python3
"""
Script to switch the CHROMADB_STORAGE_MODE environment variable for the upload lambda.

This allows easy switching between EFS and S3 modes for testing and deployment.
"""

import boto3
import os
import sys


def switch_upload_lambda_storage_mode(mode: str):
    """
    Switches the CHROMADB_STORAGE_MODE environment variable for the upload lambda.
    """
    lambda_client = boto3.client('lambda')
    function_name = "upload-images-dev-process-ocr-results"  # Target Lambda function

    if mode not in ["auto", "s3", "efs"]:
        print(f"Error: Invalid mode '{mode}'. Must be 'auto', 's3', or 'efs'.")
        sys.exit(1)

    try:
        response = lambda_client.get_function_configuration(FunctionName=function_name)
        environment = response.get('Environment', {}).get('Variables', {})
        
        if environment.get("CHROMADB_STORAGE_MODE") == mode:
            print(f"Storage mode is already set to '{mode}' for {function_name}. No change needed.")
            return

        environment["CHROMADB_STORAGE_MODE"] = mode

        lambda_client.update_function_configuration(
            FunctionName=function_name,
            Environment={'Variables': environment}
        )
        print(f"Successfully set CHROMADB_STORAGE_MODE to '{mode}' for Lambda: {function_name}")
        
        # Show current configuration
        print(f"\nCurrent environment variables:")
        for key, value in environment.items():
            if key in ["CHROMADB_STORAGE_MODE", "CHROMA_ROOT"]:
                print(f"  {key}: {value}")
                
    except Exception as e:
        print(f"Error updating Lambda configuration: {e}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python switch_upload_storage_mode.py <mode>")
        print("Modes: auto, s3, efs")
        print("\nMode descriptions:")
        print("  auto: Use EFS if available, fallback to S3 (recommended)")
        print("  s3:   Force S3-only mode (ignore EFS)")
        print("  efs:  Force EFS mode (fail if EFS not available)")
        sys.exit(1)
    
    mode_to_set = sys.argv[1].lower()
    switch_upload_lambda_storage_mode(mode_to_set)
