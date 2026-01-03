"""
Infrastructure for the job training metrics API endpoint.

GET /jobs/{job_id}/training-metrics

Returns F1 scores, confusion matrices, and per-label metrics for a training job.
"""

import json
import os

import pulumi
import pulumi_aws as aws
from pulumi import AssetArchive, FileArchive

# Import the DynamoDB table from the dynamo_db module
from dynamo_db import dynamodb_table

# Import the Lambda Layer from the lambda_layer module
from infra.components.lambda_layer import dynamo_layer

# Reference the directory containing index.py
HANDLER_DIR = os.path.join(os.path.dirname(__file__), "handler")
# Get the route name from the directory name
ROUTE_NAME = os.path.basename(os.path.dirname(__file__))
# Get the DynamoDB table name
DYNAMODB_TABLE_NAME = dynamodb_table.name


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

# IAM policy with permissions for DynamoDB operations
# Needs Query for listing metrics and GetItem for fetching job details
lambda_policy = aws.iam.Policy(
    f"api_{ROUTE_NAME}_lambda_policy",
    description="IAM policy for job training metrics Lambda to query DynamoDB",
    policy=dynamodb_table.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:Query",
                            "dynamodb:GetItem",
                            "dynamodb:DescribeTable",
                        ],
                        "Resource": [
                            arn,
                            f"{arn}/index/GSITYPE",
                            f"{arn}/index/GSI1",
                        ],
                    }
                ],
            }
        )
    ),
)

lambda_role_policy_attachment = aws.iam.RolePolicyAttachment(
    f"api_{ROUTE_NAME}_lambda_policy_attachment",
    role=lambda_role.name,
    policy_arn=lambda_policy.arn,
)

# Attach CloudWatch Logs basic execution role
aws.iam.RolePolicyAttachment(
    f"api_{ROUTE_NAME}_lambda_basic_execution",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)

# Create the Lambda function
job_training_metrics_lambda = aws.lambda_.Function(
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
            "DYNAMODB_TABLE_NAME": DYNAMODB_TABLE_NAME,
        }
    },
    memory_size=1024,
    timeout=60 * 2,  # 2 minutes for potentially large metric queries
    tags={"environment": pulumi.get_stack()},
)

# CloudWatch log group for the Lambda function
log_group = aws.cloudwatch.LogGroup(
    f"api_{ROUTE_NAME}_lambda_log_group",
    name=job_training_metrics_lambda.name.apply(lambda name: f"/aws/lambda/{name}"),
    retention_in_days=30,
)
