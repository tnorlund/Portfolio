#!/usr/bin/env python3
"""
Script to easily switch ChromaDB storage mode between S3-only and EFS modes.

Usage:
    python scripts/switch_storage_mode.py s3     # Force S3-only mode
    python scripts/switch_storage_mode.py efs    # Force EFS mode  
    python scripts/switch_storage_mode.py auto   # Auto-detect mode
    python scripts/switch_storage_mode.py status # Show current mode
"""

import boto3
import sys
import argparse
from typing import Optional

def get_lambda_function_name() -> str:
    """Get the ChromaDB Lambda function name."""
    return "chromadb-dev-docker-dev"

def update_lambda_environment_variable(mode: str) -> bool:
    """Update the CHROMADB_STORAGE_MODE environment variable."""
    lambda_client = boto3.client('lambda')
    function_name = get_lambda_function_name()
    
    try:
        # Get current configuration
        response = lambda_client.get_function_configuration(FunctionName=function_name)
        current_env = response.get('Environment', {}).get('Variables', {})
        
        # Update the storage mode
        current_env['CHROMADB_STORAGE_MODE'] = mode
        
        # Update the Lambda function
        lambda_client.update_function_configuration(
            FunctionName=function_name,
            Environment={'Variables': current_env}
        )
        
        print(f"✅ Successfully updated {function_name} to use '{mode}' mode")
        return True
        
    except Exception as e:
        print(f"❌ Failed to update Lambda function: {e}")
        return False

def get_current_mode() -> Optional[str]:
    """Get the current storage mode."""
    lambda_client = boto3.client('lambda')
    function_name = get_lambda_function_name()
    
    try:
        response = lambda_client.get_function_configuration(FunctionName=function_name)
        env_vars = response.get('Environment', {}).get('Variables', {})
        return env_vars.get('CHROMADB_STORAGE_MODE', 'auto')
    except Exception as e:
        print(f"❌ Failed to get current mode: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description='Switch ChromaDB storage mode')
    parser.add_argument('mode', nargs='?', choices=['s3', 'efs', 'auto', 'status'], 
                       help='Storage mode: s3 (S3-only), efs (EFS), auto (auto-detect), or status')
    
    args = parser.parse_args()
    
    if args.mode == 'status':
        current_mode = get_current_mode()
        if current_mode:
            print(f"Current storage mode: {current_mode}")
            
            # Show what each mode means
            mode_descriptions = {
                's3': 'S3-only mode - Downloads snapshots from S3, ignores EFS',
                'efs': 'EFS mode - Uses EFS cache, falls back to S3 if needed',
                'auto': 'Auto-detect mode - Uses EFS if available, otherwise S3'
            }
            
            print(f"Description: {mode_descriptions.get(current_mode, 'Unknown mode')}")
        return
    
    if not args.mode:
        print("❌ Please specify a mode: s3, efs, auto, or status")
        print("\nUsage examples:")
        print("  python scripts/switch_storage_mode.py s3     # Force S3-only")
        print("  python scripts/switch_storage_mode.py efs    # Force EFS")
        print("  python scripts/switch_storage_mode.py auto   # Auto-detect")
        print("  python scripts/switch_storage_mode.py status # Show current")
        return
    
    # Validate mode
    valid_modes = ['s3', 'efs', 'auto']
    if args.mode not in valid_modes:
        print(f"❌ Invalid mode '{args.mode}'. Must be one of: {', '.join(valid_modes)}")
        return
    
    # Show current mode before change
    current_mode = get_current_mode()
    if current_mode:
        print(f"Current mode: {current_mode}")
    
    # Update the mode
    success = update_lambda_environment_variable(args.mode)
    
    if success:
        print(f"\n🎯 Mode changed to '{args.mode}'")
        print("\nMode descriptions:")
        print("  s3   - S3-only mode: Downloads snapshots from S3, ignores EFS")
        print("  efs  - EFS mode: Uses EFS cache, falls back to S3 if needed") 
        print("  auto - Auto-detect: Uses EFS if available, otherwise S3")
        print("\n💡 The change will take effect on the next Lambda invocation.")
        print("   Upload some images to test the new mode!")

if __name__ == '__main__':
    main()
