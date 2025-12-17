import json
import os
from typing import Optional

import pulumi
import pulumi_aws as aws

# Import the Lambda Layer (if needed, though this endpoint doesn't use DynamoDB directly)
from infra.components.lambda_layer import dynamo_layer
from pulumi import AssetArchive, FileArchive, Input, Output

# Reference the directory containing index.py
HANDLER_DIR = os.path.join(os.path.dirname(__file__), "handler")
# Get the route name from the directory name
ROUTE_NAME = os.path.basename(os.path.dirname(__file__))

# Get stack configuration
stack = pulumi.get_stack()

# Module-level variable to hold the Lambda function
# This will be set when create_layoutlm_inference_lambda is called in __main__.py
layoutlm_inference_lambda: Optional[aws.lambda_.Function] = None

# IAM role for the Lambda function - created when Lambda is created
lambda_role: Optional[aws.iam.Role] = None


def create_layoutlm_inference_lambda(
    cache_bucket_name: Input[str],
) -> aws.lambda_.Function:
    """Create the LayoutLM inference API Lambda function.

    This function should only be called after the cache bucket exists.
    No placeholder bucket names are used - the real bucket name must be provided.

    Args:
        cache_bucket_name: The actual S3 bucket name for the cache (must be real, not placeholder)

    Returns:
        The created Lambda function
    """
    global lambda_role, layoutlm_inference_lambda

    # Create IAM role for the Lambda function
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
    s3_policy = aws.iam.RolePolicy(
        f"api_{ROUTE_NAME}_s3_policy",
        role=lambda_role.id,
        policy=Output.from_input(cache_bucket_name).apply(
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
    )

    # Attach basic execution role
    aws.iam.RolePolicyAttachment(
        f"api_{ROUTE_NAME}_basic_execution",
        role=lambda_role.name,
        policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )

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
        layers=[dynamo_layer.arn] if dynamo_layer.arn else None,
        environment=Output.from_input(cache_bucket_name).apply(
            lambda bucket: aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "S3_CACHE_BUCKET": bucket,
                }
            )
        ),
        memory_size=256,
        timeout=30,  # Should be very fast since it's just reading from S3
        tags={"environment": stack},
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

    return layoutlm_inference_lambda

