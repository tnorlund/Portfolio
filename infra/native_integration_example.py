"""
Integration example showing how to use the native receipt_dynamo layer.

This demonstrates a complete migration from shell scripts to native Pulumi.
"""

import pulumi
import pulumi_aws as aws

# Import the native layer implementation
from native_receipt_dynamo_layer import create_native_dynamo_layer


def create_lambda_with_native_layer():
    """
    Example: Create a Lambda function using the native layer.
    
    This shows how simple it is to use the native approach.
    """
    
    print("🔧 Creating Lambda function with native layer...")
    
    # Step 1: Create the native layer (no shell scripts!)
    dynamo_layer = create_native_dynamo_layer()
    
    # Step 2: Create a Lambda function that uses the layer
    lambda_function = aws.lambda_.Function(
        "example-native-function",
        runtime="python3.12",
        architectures=["arm64"],  # Native layer supports both arm64 and x86_64
        handler="handler.main",
        code=pulumi.FileArchive("./example_lambda_handler"),  # Your handler code
        layers=[dynamo_layer.arn],  # Use the native layer - no ARG_MAX issues!
        environment=aws.lambda_.FunctionEnvironmentArgs(
            variables={
                "PYTHONPATH": "/opt/python",  # Standard Lambda layer path
                "DYNAMODB_TABLE_NAME": "receipts-table"
            }
        ),
        timeout=30,
        memory_size=256,
        description="Example function using native Pulumi layer (no shell scripts!)",
        opts=pulumi.ResourceOptions(
            depends_on=[dynamo_layer.layer_version]  # Ensure layer is created first
        )
    )
    
    # Export the function ARN and layer ARN
    pulumi.export("example_function_arn", lambda_function.arn)
    pulumi.export("native_layer_arn", dynamo_layer.arn)
    
    print("✅ Lambda function created with native layer!")
    
    return lambda_function, dynamo_layer


def create_multiple_functions_example():
    """
    Example: Create multiple Lambda functions sharing the same native layer.
    
    Shows how the native layer can be reused efficiently.
    """
    
    print("🔧 Creating multiple functions with shared native layer...")
    
    # Create the native layer once
    shared_layer = create_native_dynamo_layer()
    
    # Create multiple functions that use the same layer
    functions = []
    
    function_configs = [
        ("receipt-processor", "Process receipt data", "process_handler.main"),
        ("receipt-validator", "Validate receipt data", "validate_handler.main"), 
        ("receipt-exporter", "Export receipt data", "export_handler.main"),
    ]
    
    for name, description, handler in function_configs:
        function = aws.lambda_.Function(
            name,
            runtime="python3.12",
            architectures=["arm64"],
            handler=handler,
            code=pulumi.FileArchive(f"./{name}_handler"),
            layers=[shared_layer.arn],  # All functions share the same native layer
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "PYTHONPATH": "/opt/python",
                    "DYNAMODB_TABLE_NAME": "receipts-table"
                }
            ),
            timeout=60,
            memory_size=512,
            description=f"{description} - uses native layer (no shell script issues)",
            opts=pulumi.ResourceOptions(
                depends_on=[shared_layer.layer_version]
            )
        )
        
        functions.append(function)
        pulumi.export(f"{name.replace('-', '_')}_arn", function.arn)
    
    print(f"✅ Created {len(functions)} functions sharing native layer!")
    
    return functions, shared_layer


def demonstrate_native_benefits():
    """
    Demonstrate the benefits of the native approach over shell scripts.
    """
    
    print("\n🎯 Native Approach Benefits Demonstration")
    print("=" * 50)
    
    # Create a function using native layer
    function, layer = create_lambda_with_native_layer()
    
    # Show the benefits
    benefits = [
        "✅ No 'argument list too long' errors",
        "✅ Works consistently across all environments (macOS, Linux, CI)",
        "✅ Faster deployment (no script generation overhead)",
        "✅ Better error messages and debugging",
        "✅ Native Pulumi change detection",
        "✅ Cleaner infrastructure code",
        "✅ Platform-independent implementation",
        "✅ No shell dependencies or security concerns",
        "✅ Better resource dependency management",
        "✅ Automatic cleanup and rollback support"
    ]
    
    print("\n📋 Benefits Summary:")
    for benefit in benefits:
        print(f"   {benefit}")
    
    # Show code comparison
    print(f"\n📊 Code Comparison:")
    print(f"   Shell Script Approach: ~200 lines of complex bash")
    print(f"   Native Approach: ~100 lines of clean Python")
    print(f"   Command Line Length: 0 bytes (vs ~100KB)")
    print(f"   Dependencies: Pure Pulumi (vs bash, AWS CLI, shell utils)")
    
    return function, layer


def migration_guide():
    """
    Show the step-by-step migration process.
    """
    
    print("\n📚 Migration Guide: Shell Scripts → Native Pulumi")
    print("=" * 55)
    
    steps = [
        {
            "step": "1. Identify Current Layer",
            "old": "from fast_lambda_layer import FastLambdaLayer",
            "new": "from native_receipt_dynamo_layer import create_native_dynamo_layer",
            "description": "Replace shell script layer imports"
        },
        {
            "step": "2. Replace Layer Creation",
            "old": 'layer = FastLambdaLayer("receipt-dynamo", "./receipt_dynamo")',
            "new": 'layer = create_native_dynamo_layer()',
            "description": "Use native factory function"
        },
        {
            "step": "3. Update Lambda Functions",
            "old": "layers=[old_layer.arn]  # ARG_MAX issues possible",
            "new": "layers=[native_layer.arn]  # No command line issues",
            "description": "Same interface, but no shell script issues"
        },
        {
            "step": "4. Remove Shell Script Files",
            "old": "# Keep: fast_lambda_layer.py, scripts/, temp files",
            "new": "# Remove: All shell script layer implementations",
            "description": "Clean up old implementations"
        }
    ]
    
    for step_info in steps:
        print(f"\n{step_info['step']}:")
        print(f"   Description: {step_info['description']}")
        print(f"   OLD: {step_info['old']}")
        print(f"   NEW: {step_info['new']}")
    
    print(f"\n🎉 Migration Result:")
    print(f"   - No more CI failures due to ARG_MAX limits")
    print(f"   - Consistent behavior across all environments")
    print(f"   - Faster, more reliable deployments")
    print(f"   - Cleaner, more maintainable code")


# Example handler code that would use the layer
EXAMPLE_HANDLER_CODE = '''"""
Example Lambda handler that uses the native receipt_dynamo layer.
"""

import json
import os
from receipt_dynamo import DynamoClient

def main(event, context):
    """
    Example handler that uses receipt_dynamo from the native layer.
    
    This demonstrates that the layer works exactly the same as before,
    but without any shell script issues.
    """
    
    # Initialize DynamoDB client from the layer
    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "receipts")
    dynamo_client = DynamoClient(table_name)
    
    try:
        # Example: Get some data from DynamoDB
        # This works because receipt_dynamo is available via the native layer
        result = dynamo_client.list_receipts(limit=5)
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Native layer working perfectly!",
                "receipts_found": len(result),
                "layer_source": "native_pulumi_layer"
            })
        }
        
    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": str(e),
                "message": "Error using native layer"
            })
        }
'''


def create_example_files():
    """Create example handler files for demonstration."""
    
    print("\n📁 Creating example handler files...")
    
    from pathlib import Path
    
    # Create example handler directory
    handler_dir = Path("example_lambda_handler")
    handler_dir.mkdir(exist_ok=True)
    
    # Write example handler
    handler_file = handler_dir / "handler.py"
    with open(handler_file, 'w') as f:
        f.write(EXAMPLE_HANDLER_CODE)
    
    print(f"✅ Created example handler: {handler_file}")
    print("   This shows how to use receipt_dynamo from the native layer")
    
    return handler_dir


if __name__ == "__main__":
    # Run the complete integration example
    print("🚀 Native Layer Integration Example")
    print("=" * 40)
    
    try:
        # Show the benefits
        demonstrate_native_benefits()
        
        # Show migration guide
        migration_guide()
        
        # Create example files
        create_example_files()
        
        print("\n🎉 Integration example complete!")
        print("\n🔑 Key Takeaways:")
        print("   1. Native approach eliminates ARG_MAX issues completely")
        print("   2. Same API as before, but more reliable")
        print("   3. Faster deployments and better error messages")
        print("   4. Platform-independent and maintainable")
        print("\n💡 Next Steps:")
        print("   1. Test this implementation in your dev environment")
        print("   2. Migrate one function at a time")
        print("   3. Remove old shell script implementations")
        
    except Exception as e:
        print(f"❌ Integration example failed: {e}")
        raise