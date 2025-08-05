#!/usr/bin/env python3
"""
Trigger the ChromaDB step function to process embeddings.
"""

import os
import sys
import json
import boto3
from datetime import datetime

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


def list_all_step_functions():
    """List all available step functions."""
    sfn_client = boto3.client('stepfunctions')
    response = sfn_client.list_state_machines()
    
    current_stack = get_pulumi_stack()
    
    print(f"\nAvailable Step Functions (current stack: {current_stack}):")
    print("-" * 80)
    
    # Group by likely stack
    by_stack = {'dev': [], 'prod': [], 'other': []}
    
    for sm in response['stateMachines']:
        name = sm['name']
        if '-dev' in name.lower() or name.lower().endswith('dev'):
            by_stack['dev'].append(sm)
        elif '-prod' in name.lower() or name.lower().endswith('prod'):
            by_stack['prod'].append(sm)
        else:
            by_stack['other'].append(sm)
    
    for stack, functions in by_stack.items():
        if functions:
            print(f"\n{stack.upper()} Stack:")
            for sm in functions:
                is_current = '→' if stack == current_stack else ' '
                print(f"{is_current} {sm['name']}")
    
    print("-" * 80)
    return response['stateMachines']


def get_pulumi_stack():
    """Get the current Pulumi stack name."""
    try:
        import subprocess
        result = subprocess.run(
            ["pulumi", "stack", "--show-name"],
            capture_output=True,
            text=True,
            cwd="infra"
        )
        if result.returncode == 0:
            stack_name = result.stdout.strip()
            # Extract just the stack name (dev, prod, etc.)
            if '/' in stack_name:
                return stack_name.split('/')[-1]
            return stack_name
    except:
        pass
    
    # Fallback: check environment variable
    stack = os.environ.get('PULUMI_STACK', 'dev')
    print(f"Warning: Could not get Pulumi stack, assuming '{stack}'")
    return stack


def get_step_function_arn(step_function_type='chromadb'):
    """Get the step function ARN from CloudFormation/Pulumi stack."""
    sfn_client = boto3.client('stepfunctions')
    response = sfn_client.list_state_machines()
    
    # Get current stack
    stack = get_pulumi_stack()
    print(f"Looking for {step_function_type} step function in '{stack}' stack...")
    
    # Different patterns to search for
    patterns = {
        'chromadb': ['chromadb', 'word-label'],
        'submit': ['submit-completion-batch', 'step-func-submit'],
        'poll': ['poll-completion-batch', 'step-func-poll']
    }
    
    search_patterns = patterns.get(step_function_type, [step_function_type])
    
    # First try to find with stack suffix
    for sm in response['stateMachines']:
        name_lower = sm['name'].lower()
        # Check if this is the right stack
        if stack.lower() in name_lower or name_lower.endswith(f"-{stack.lower()}"):
            for pattern in search_patterns:
                if pattern in name_lower:
                    return sm['stateMachineArn'], sm['name']
    
    # If not found with stack suffix, try without (for legacy names)
    for sm in response['stateMachines']:
        name_lower = sm['name'].lower()
        for pattern in search_patterns:
            if pattern in name_lower:
                print(f"Warning: Found step function without stack suffix: {sm['name']}")
                return sm['stateMachineArn'], sm['name']
    
    # If not found, list all available
    print(f"\nCould not find step function matching patterns: {search_patterns} for stack: {stack}")
    list_all_step_functions()
    raise ValueError(f"Could not find {step_function_type} step function. Make sure it's deployed.")


def trigger_step_function(step_function_type='chromadb'):
    """Trigger a step function."""
    sfn_client = boto3.client('stepfunctions')
    
    # Get the step function ARN
    step_function_arn, step_function_name = get_step_function_arn(step_function_type)
    print(f"Found step function: {step_function_name}")
    
    # Start execution
    execution_name = f"{step_function_type}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    
    try:
        response = sfn_client.start_execution(
            stateMachineArn=step_function_arn,
            name=execution_name,
            input='{}'  # Empty input, the step function will query for pending batches
        )
        
        print(f"\nStep function execution started!")
        print(f"Execution Name: {execution_name}")
        print(f"Execution ARN: {response['executionArn']}")
        print(f"\nYou can monitor the execution in the AWS console:")
        print(f"https://console.aws.amazon.com/states/home?region={boto3.Session().region_name}#/executions/details/{response['executionArn']}")
        
    except Exception as e:
        print(f"Error starting step function: {e}")
        sys.exit(1)


def check_pending_batches():
    """Check if there are pending embedding batches."""
    from receipt_dynamo import DynamoClient
    from receipt_dynamo.entities.embedding_batch_result import EmbeddingBatchResult
    
    client = DynamoClient()
    
    # Query for pending batches
    pending_batches = client.query_pending_embedding_batches()
    
    if not pending_batches:
        print("No pending embedding batches found.")
        print("You may need to run reset_line_embeddings.py first to create new batches.")
        return False
    
    print(f"Found {len(pending_batches)} pending embedding batches:")
    for batch in pending_batches[:5]:
        print(f"  - Batch {batch.batch_id}: {batch.openai_batch_id}")
    
    if len(pending_batches) > 5:
        print(f"  ... and {len(pending_batches) - 5} more")
    
    return True


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Trigger step functions for embedding processing"
    )
    parser.add_argument(
        "--type",
        choices=['chromadb', 'submit', 'poll', 'list'],
        default='chromadb',
        help="Which step function to trigger (default: chromadb)"
    )
    parser.add_argument(
        "--skip-check",
        action="store_true",
        help="Skip checking for pending batches"
    )
    
    args = parser.parse_args()
    
    print(f"Step Function Trigger: {args.type}")
    print("=" * 50)
    
    # Check for required environment variable
    if not os.environ.get('DYNAMODB_TABLE_NAME'):
        print("ERROR: DYNAMODB_TABLE_NAME environment variable not set")
        print("Run: export DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22")
        sys.exit(1)
    
    # Special case: just list all step functions
    if args.type == 'list':
        list_all_step_functions()
        sys.exit(0)
    
    # Check for pending batches unless skipped
    if not args.skip_check and args.type in ['chromadb', 'poll']:
        print("\nChecking for pending embedding batches...")
        if not check_pending_batches():
            print("\nNo work to do. Exiting.")
            sys.exit(0)
    
    print(f"\nTriggering {args.type} step function...")
    trigger_step_function(args.type)


if __name__ == "__main__":
    main()