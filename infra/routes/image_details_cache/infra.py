"""Pulumi infrastructure for image details cache API Lambda."""

import json
import os

import pulumi
import pulumi_aws as aws
from pulumi import AssetArchive, FileArchive, Input, Output

# Import the Lambda Layer
from infra.components.lambda_layer import dynamo_layer

# Import the cache bucket name from the cache generator route
from routes.image_details_cache_generator.infra import cache_bucket_name

# Reference the directory containing index.py
HANDLER_DIR = os.path.join(os.path.dirname(__file__), "handler")
# Get the route name from the directory name
ROUTE_NAME = os.path.basename(os.path.dirname(__file__))

# Get stack configuration
stack = pulumi.get_stack()


# Define the IAM role for the Lambda function
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

# IAM policy for S3 read access to cache bucket
s3_policy = aws.iam.Policy(
    f"api_{ROUTE_NAME}_s3_policy",
    description="IAM policy for Lambda to read S3 image details cache",
    policy=cache_bucket_name.apply(
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

# Attach policies to role
aws.iam.RolePolicyAttachment(
    f"api_{ROUTE_NAME}_s3_policy_attachment",
    role=lambda_role.name,
    policy_arn=s3_policy.arn,
)

# Attach basic execution role
aws.iam.RolePolicyAttachment(
    f"api_{ROUTE_NAME}_basic_execution",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)


def create_image_details_cache_lambda(
    cache_bucket_name: Input[str],
) -> aws.lambda_.Function:
    """Create the image details cache API Lambda function."""
    image_details_cache_lambda = aws.lambda_.Function(
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
        layers=[dynamo_layer.arn],
        environment={
            "variables": {
                "S3_CACHE_BUCKET": Output.from_input(cache_bucket_name),
            }
        },
        memory_size=256,
        timeout=30,
        tags={"environment": stack},
    )

    return image_details_cache_lambda


# Create the Lambda function instance
image_details_cache_lambda = create_image_details_cache_lambda(
    cache_bucket_name=cache_bucket_name,
)

# CloudWatch log group for the Lambda function
log_group = aws.cloudwatch.LogGroup(
    f"api_{ROUTE_NAME}_lambda_log_group",
    name=image_details_cache_lambda.name.apply(
        lambda function_name: f"/aws/lambda/{function_name}"
    ),
    retention_in_days=30,
)

# Export Lambda details
pulumi.export(f"{ROUTE_NAME}_lambda_arn", image_details_cache_lambda.arn)
pulumi.export(f"{ROUTE_NAME}_lambda_name", image_details_cache_lambda.name)
