#!/usr/bin/env python3
"""
test_fast_lambda_layer.py

Minimal Pulumi stack for testing FastLambdaLayer independently.
This creates only the necessary resources to test layer building.
"""

import pulumi
import pulumi_aws as aws
from fast_lambda_layer import FastLambdaLayer

# Get the current stack - this will be unique per user/branch
stack = pulumi.get_stack()
config = pulumi.Config()

# Test configuration - you can override these with pulumi config
TEST_PACKAGE = config.get("test-package") or "receipt_dynamo"
TEST_SYNC_MODE = config.get_bool("sync-mode") or False
TEST_FORCE_REBUILD = config.get_bool("force-rebuild") or False

# Create a unique resource prefix to avoid any conflicts
# Stack name already includes user and branch, but let's be extra safe
RESOURCE_PREFIX = f"isolated-{stack[:20]}" if len(stack) > 20 else f"isolated-{stack}"

# Create a single test layer with isolated naming
test_layer = FastLambdaLayer(
    name=f"{RESOURCE_PREFIX}-layer",
    package_dir=TEST_PACKAGE,
    python_versions=["3.12"],
    description=f"Isolated test layer for {TEST_PACKAGE} on stack {stack}",
    needs_pillow=False,
    sync_mode=TEST_SYNC_MODE,
)

# Export the layer name for reference
pulumi.export("test_layer_name", test_layer.name)
pulumi.export("test_layer_arn", test_layer.arn)
pulumi.export("test_package", TEST_PACKAGE)
pulumi.export("sync_mode", TEST_SYNC_MODE)
pulumi.export("force_rebuild", TEST_FORCE_REBUILD)

# Optional: Create a simple Lambda function to test the layer
test_lambda_role = aws.iam.Role(
    f"{RESOURCE_PREFIX}-lambda-role",
    assume_role_policy="""{
        "Version": "2012-10-17",
        "Statement": [{
            "Action": "sts:AssumeRole",
            "Principal": {
                "Service": "lambda.amazonaws.com"
            },
            "Effect": "Allow"
        }]
    }"""
)

# Attach basic execution policy
aws.iam.RolePolicyAttachment(
    f"{RESOURCE_PREFIX}-lambda-execution",
    role=test_lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/"
               "AWSLambdaBasicExecutionRole"
)

# Create a test Lambda function that uses the layer
test_lambda = aws.lambda_.Function(
    f"{RESOURCE_PREFIX}-test-function",
    role=test_lambda_role.arn,
    runtime="python3.12",
    handler="index.handler",
    code=pulumi.AssetArchive({
        "index.py": pulumi.StringAsset("""
def handler(event, context):
    # Try to import from the layer
    try:
        import receipt_dynamo
        return {
            'statusCode': 200,
            'body': f'Successfully imported receipt_dynamo version: '
                    f'{receipt_dynamo.__version__}'
        }
    except ImportError as e:
        return {
            'statusCode': 500,
            'body': f'Failed to import: {str(e)}'
        }
""")
    }),
    layers=[test_layer.arn] if test_layer.arn else [],
    timeout=30,
    memory_size=128,
    architectures=["arm64"],
    tags={
        "environment": stack,
        "test": "fast-lambda-layer"
    }
)

pulumi.export("test_lambda_name", test_lambda.name)
pulumi.export("test_lambda_arn", test_lambda.arn)
