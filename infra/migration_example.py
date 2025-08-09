"""
Example of how to migrate from custom scripts to native Pulumi components.
"""

# BEFORE: Custom script approach (problematic)
def old_lambda_layer():
    from fast_lambda_layer import FastLambdaLayer
    
    return FastLambdaLayer(
        name="receipt-dynamo",
        package_path="./receipt_dynamo", 
        # This creates huge shell scripts that exceed ARG_MAX
    )

# AFTER: Native Pulumi approach (clean)
def new_lambda_layer():
    from native_lambda_layer import NativeLambdaLayer
    
    return NativeLambdaLayer(
        name="receipt-dynamo",
        package_path="./receipt_dynamo",
        # No shell scripts, pure Pulumi resources
    )

# Migration steps:
def migrate_layers():
    """Step-by-step migration from custom scripts to native components."""
    
    # Step 1: Simple layers (no complex dependencies)
    dynamo_layer = NativeLambdaLayer(
        name="receipt-dynamo",
        package_path="./receipt_dynamo"
    )
    
    # Step 2: Complex layers (with CodeBuild)
    label_layer = NativeLambdaLayerWithCodeBuild(
        name="receipt-label",
        package_path="./receipt_label", 
        needs_build=True  # Has complex pip dependencies
    )
    
    # Step 3: Update Lambda functions to use new layers
    lambda_function = aws.lambda_.Function(
        "receipt-processor",
        runtime="python3.12",
        architectures=["arm64"],
        layers=[
            dynamo_layer.arn,  # References native layer
            label_layer.arn    # References native layer
        ]
    )
    
    return {
        "dynamo_layer": dynamo_layer,
        "label_layer": label_layer,
        "function": lambda_function
    }