import json
import os
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import AssetArchive, FileArchive, Input, Output

# Import the Lambda Layer (if needed, though this endpoint doesn't use DynamoDB directly)
from lambda_layer import dynamo_layer

# Import the cache bucket name from the cache generator route
# This will be None initially and set in __main__.py when the cache generator is created
from routes.layoutlm_inference_cache_generator.infra import cache_bucket_name

# Module-level variable that can be updated
cache_bucket_name = cache_bucket_name

# Reference the directory containing index.py
HANDLER_DIR = os.path.join(os.path.dirname(__file__), "handler")
# Get the route name from the directory name
ROUTE_NAME = os.path.basename(os.path.dirname(__file__))

# Get stack configuration
stack = pulumi.get_stack()

# Handle case where cache_bucket_name might be None (if cache generator not created)
# Use a placeholder bucket name that will be replaced when cache generator is created
# The cache_bucket_name will be set in __main__.py when the cache generator is created
# We use Output.from_input to handle both None and Output cases
# When cache_bucket_name is updated in __main__.py, this will automatically update
_cache_bucket_name = (
    cache_bucket_name
    if cache_bucket_name is not None
    else Output.from_input("placeholder-bucket-name")
)

# Define the IAM role for the Lambda function (at module level)
lambda_role = aws.iam.Role(
    f"api_{ROUTE_NAME}_lambda_role",
    assume_role_policy="""{
        "Version": "2012-10-17",
        "Statement": [
            {
                "Action": "sts:AssumeRole",
                "Principal": {
                    "Service": "lambda.amazonaws.com"
                },
                "Effect": "Allow",
                "Sid": ""
            }
        ]
    }""",
)

# IAM inline policy for S3 read access to cache bucket
# This will be created with a placeholder initially, then updated in __main__.py
# when the cache bucket is created
s3_policy = aws.iam.RolePolicy(
    f"api_{ROUTE_NAME}_s3_policy",
    role=lambda_role.id,
    policy=_cache_bucket_name.apply(
        lambda bucket: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["s3:GetObject", "s3:ListBucket"],
                        "Resource": [
                            f"arn:aws:s3:::{bucket}/*",
                            f"arn:aws:s3:::{bucket}",
                        ],
                    }
                ],
            }
        )
    ),
    # Allow the policy to be replaced when the bucket name changes
    opts=pulumi.ResourceOptions(replace_on_changes=["policy"]),
)

# Attach basic execution role
aws.iam.RolePolicyAttachment(
    f"api_{ROUTE_NAME}_basic_execution",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)


def create_layoutlm_inference_lambda(
    cache_bucket_name: Input[str],
) -> aws.lambda_.Function:
    """Create the LayoutLM inference API Lambda function."""
    # Create the Lambda function
    layoutlm_inference_lambda = aws.lambda_.Function(
        f"api_{ROUTE_NAME}_GET_lambda",
        runtime="python3.12",
        architectures=["arm64"],
        role=lambda_role.arn,
        code=AssetArchive(
            {
                ".": FileArchive(HANDLER_DIR),
            }
        ),
        handler="index.handler",
        layers=[dynamo_layer.arn],  # Keep for consistency, even if not strictly needed
        environment=_cache_bucket_name.apply(
            lambda bucket: {
                "variables": {
                    "S3_CACHE_BUCKET": bucket,
                }
            }
        ),
        memory_size=256,
        timeout=30,  # Should be very fast since it's just reading from S3
        tags={"environment": stack},
    )

    return layoutlm_inference_lambda


# Create the Lambda function instance using cache bucket name
# Use the cache bucket name if available, otherwise use a placeholder
# The placeholder will be replaced when the cache generator is created
layoutlm_inference_lambda = create_layoutlm_inference_lambda(
    cache_bucket_name=_cache_bucket_name,
)

# CloudWatch log group for the Lambda function
log_group = aws.cloudwatch.LogGroup(
    f"api_{ROUTE_NAME}_lambda_log_group",
    name=layoutlm_inference_lambda.name.apply(
        lambda function_name: f"/aws/lambda/{function_name}"
    ),
    retention_in_days=30,
)

# Export Lambda details
pulumi.export(f"{ROUTE_NAME}_lambda_arn", layoutlm_inference_lambda.arn)
pulumi.export(f"{ROUTE_NAME}_lambda_name", layoutlm_inference_lambda.name)

