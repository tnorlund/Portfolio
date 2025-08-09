#!/usr/bin/env python3
"""
Test deployment for FastLambdaLayer only
"""

import pulumi
from fast_lambda_layer import FastLambdaLayer

# Get configuration
config = pulumi.Config()
stack = pulumi.get_stack()

# Create just one test layer with a shortened name to avoid AWS limits
test_layer = FastLambdaLayer(
    name="test",  # Short name to avoid bucket name limits
    package_dir="receipt_dynamo",
    python_versions=["3.12"],
    description="Test layer for receipt_dynamo",
    needs_pillow=False,
    sync_mode=True,  # Use sync mode to see results immediately
)

# Export info
pulumi.export("layer_name", test_layer.name)
pulumi.export("stack", stack)
pulumi.export("test_complete", True)