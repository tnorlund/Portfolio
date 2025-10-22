"""
Top-level conftest for infra tests.

Mocks infrastructure-specific modules before any package imports happen.
"""

import sys
import os
from unittest.mock import MagicMock

# Set test mode FIRST
os.environ["PYTEST_RUNNING"] = "1"

# Mock pulumi module - make get_stack() throw to prevent layer building
mock_pulumi = MagicMock()
mock_pulumi.get_stack.side_effect = Exception("Not in Pulumi context - testing mode")
sys.modules["pulumi"] = mock_pulumi

# Create comprehensive mock for pulumi_aws that returns MagicMocks for all attributes
class InfrastructureMock(MagicMock):
    """Enhanced mock that returns MagicMocks for any attribute access."""
    def __getattr__(self, name):
        if name.startswith('_'):
            return super().__getattr__(name)
        # Return a new MagicMock for any attribute
        return MagicMock()

mock_pulumi_aws = InfrastructureMock()
sys.modules["pulumi_aws"] = mock_pulumi_aws
sys.modules["pulumi_aws.ecr"] = MagicMock()
sys.modules["pulumi_aws.ecs"] = MagicMock()
sys.modules["pulumi_aws.iam"] = MagicMock()
sys.modules["pulumi_aws.lambda_"] = MagicMock()
sys.modules["pulumi_aws.sqs"] = MagicMock()
sys.modules["pulumi_aws.s3"] = MagicMock()

sys.modules["pulumi_docker_build"] = MagicMock()
sys.modules["codebuild_docker_image"] = MagicMock()
sys.modules["ecs_lambda"] = MagicMock()

# Mock lambda_layer completely - prevent any code execution
mock_lambda_layer = MagicMock()
mock_lambda_layer.SKIP_LAYER_BUILDING = True
mock_lambda_layer.dynamo_layer = MagicMock()
mock_lambda_layer.label_layer = MagicMock()
mock_lambda_layer.upload_layer = MagicMock()
mock_lambda_layer.lambda_layers = {}
# Prevent attribute errors
mock_lambda_layer.__getattr__ = lambda self, name: MagicMock()
sys.modules["lambda_layer"] = mock_lambda_layer

# Also mock infra-level lambda_layer if it exists
sys.modules["infra.lambda_layer"] = mock_lambda_layer

