#!/usr/bin/env python3
"""
simple_layer_test.py - Minimal test for FastLambdaLayer only
"""

import pulumi
from fast_lambda_layer import FastLambdaLayer

# Get configuration
config = pulumi.Config()
stack = pulumi.get_stack()

# Override the SKIP_LAYER_BUILDING flag
import fast_lambda_layer
fast_lambda_layer.SKIP_LAYER_BUILDING = False

# Create just one test layer with a shortened name to avoid AWS limits
test_layer = FastLambdaLayer(
    name="test",  # Short name to avoid bucket name limits
    package_dir="receipt_dynamo",
    python_versions=["3.12"],
    description="Test layer for receipt_dynamo",
    needs_pillow=False,
    sync_mode=config.get_bool("sync-mode") or True,  # Default to sync for testing
)

# Export info
pulumi.export("layer_name", test_layer.name)
pulumi.export("stack", stack)
pulumi.export("test_complete", True)