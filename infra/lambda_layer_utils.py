"""
Utilities for referencing the latest Lambda layer versions.

This allows Lambda functions to use the latest layer version deployed
by CodePipeline, rather than the version Pulumi originally created.
"""

import pulumi
import pulumi_aws as aws
from typing import Optional


def get_latest_layer_version_arn(layer_name: str, compatible_runtime: Optional[str] = "python3.12") -> pulumi.Output[str]:
    """
    Get the ARN of the latest version of a Lambda layer.
    
    Args:
        layer_name: The name of the Lambda layer
        compatible_runtime: The runtime to filter by (optional)
        
    Returns:
        The ARN of the latest layer version
    """
    # Get the latest layer version
    layer_version = aws.lambda_.get_layer_version(
        layer_name=layer_name,
        # This gets the latest version number
        version=aws.lambda_.get_layer_version_output(
            layer_name=layer_name,
            compatible_runtime=compatible_runtime,
        ).version,
        compatible_runtime=compatible_runtime,
    )
    
    return pulumi.Output.from_input(layer_version.arn)


def get_latest_layer_version(layer_name: str) -> pulumi.Output[int]:
    """
    Get just the version number of the latest layer.
    
    Args:
        layer_name: The name of the Lambda layer
        
    Returns:
        The latest version number
    """
    # List all versions and get the latest
    versions = aws.lambda_.get_layer_versions(
        layer_name=layer_name,
    )
    
    # The first one in the list is the latest
    return pulumi.Output.from_input(versions.versions[0].version if versions.versions else 1)


class DynamicLayerReference:
    """
    Helper class to manage dynamic layer references for Lambda functions.
    
    This allows your Lambda functions to always use the latest layer version
    deployed by your CodePipeline, rather than a fixed version.
    """
    
    def __init__(self, stack: str, account_id: str, region: str):
        """
        Initialize the dynamic layer reference helper.
        
        Args:
            stack: The current Pulumi stack name
            account_id: AWS account ID
            region: AWS region
        """
        self.stack = stack
        self.account_id = account_id
        self.region = region
        
    def get_layer_arn(self, layer_base_name: str) -> pulumi.Output[str]:
        """
        Get the latest layer ARN for a given base name.
        
        Args:
            layer_base_name: Base name like "receipt-dynamo", "receipt-label", etc.
            
        Returns:
            The full ARN of the latest layer version
        """
        # Construct the full layer name (matching your naming convention)
        layer_name = f"{layer_base_name}-layer-{self.stack}"
        
        # Method 1: Build ARN with latest version
        latest_version = get_latest_layer_version(layer_name)
        
        return pulumi.Output.format(
            "arn:aws:lambda:{0}:{1}:layer:{2}:{3}",
            self.region,
            self.account_id,
            layer_name,
            latest_version,
        )
    
    def get_all_layer_arns(self) -> dict[str, pulumi.Output[str]]:
        """
        Get all standard layer ARNs for your project.
        
        Returns:
            Dictionary mapping layer names to their latest ARNs
        """
        layers = ["receipt-dynamo", "receipt-label", "receipt-upload"]
        return {
            layer: self.get_layer_arn(layer)
            for layer in layers
        }


# Example usage function
def create_lambda_with_latest_layers(
    name: str,
    handler: str,
    role: pulumi.Input[str],
    stack: str,
    account_id: str,
    region: str,
    layers_to_use: list[str] = None,
) -> aws.lambda_.Function:
    """
    Create a Lambda function that always uses the latest layer versions.
    
    Args:
        name: Lambda function name
        handler: Handler function
        role: IAM role ARN
        stack: Pulumi stack name
        account_id: AWS account ID
        region: AWS region
        layers_to_use: List of layer base names to use (e.g., ["receipt-dynamo", "receipt-label"])
        
    Returns:
        Lambda function resource
    """
    # Initialize the dynamic layer reference helper
    layer_helper = DynamicLayerReference(stack, account_id, region)
    
    # Get the latest layer ARNs
    if layers_to_use is None:
        layers_to_use = ["receipt-dynamo", "receipt-label"]
        
    layer_arns = [
        layer_helper.get_layer_arn(layer_name)
        for layer_name in layers_to_use
    ]
    
    # Create the Lambda function
    return aws.lambda_.Function(
        name,
        runtime="python3.12",
        handler=handler,
        role=role,
        layers=layer_arns,  # This will always reference the latest versions
        # ... other Lambda configuration ...
    )