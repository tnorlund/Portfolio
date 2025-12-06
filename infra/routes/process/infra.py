import json
import os

import pulumi
import pulumi_aws as aws

# Import the DynamoDB table name from the dynamo_db module
from dynamo_db import dynamodb_table

# Import the Lambda Layer from the lambda_layer module
from infra.components.lambda_layer import dynamo_layer
from pulumi import AssetArchive, FileArchive
from raw_bucket import raw_bucket
from s3_website import site_bucket

# Reference the directory containing index.py
HANDLER_DIR = os.path.join(os.path.dirname(__file__), "handler")
# Get the route name from the directory name
ROUTE_NAME = os.path.basename(os.path.dirname(__file__))
# Get the DynamoDB table name
DYNAMODB_TABLE_NAME = dynamodb_table.name


# Define the IAM role for the Lambda function with permissions for the DynamoDB table
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

lambda_policy = aws.iam.Policy(
    f"api_{ROUTE_NAME}_lambda_policy",
    description="IAM policy for '/process' route Lambda to query DynamoDB",
    policy=pulumi.Output.all(
        dynamodb_table.arn,
        raw_bucket.arn,
        site_bucket.arn,
    ).apply(
        lambda arns: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        # ---------- DynamoDB Permissions ----------
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:Query",
                            "dynamodb:DescribeTable",
                            "dynamodb:PutItem",
                            "dynamodb:BatchWriteItem",
                        ],
                        "Resource": [
                            arns[0],  # dynamo_arn
                            f"{arns[0]}/index/GSI1",
                        ],
                    },
                    {
                        # ---------- S3 Permissions (Raw Bucket) ----------
                        "Effect": "Allow",
                        "Action": [
                            "s3:PutObject",
                            "s3:GetObject",
                            "s3:HeadObject",
                            "s3:ListBucket",
                        ],
                        "Resource": [
                            arns[1],  # raw_arn
                            f"{arns[1]}/*",
                        ],
                    },
                    {
                        # ---------- S3 Permissions (CDN Bucket) ----------
                        "Effect": "Allow",
                        "Action": [
                            "s3:PutObject",
                            "s3:GetObject",
                            "s3:HeadObject",
                            "s3:ListBucket",
                        ],
                        "Resource": [
                            arns[2],  # cdn_arn
                            f"{arns[2]}/*",
                        ],
                    },
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

# Attach the necessary policies to the role
aws.iam.RolePolicyAttachment(
    f"api_{ROUTE_NAME}_lambda_basic_execution",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)

# Create the Lambda function for the "user" route
process_lambda = aws.lambda_.Function(
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
    memory_size=3072,
    timeout=300,
    tags={"environment": pulumi.get_stack()},
)

# CloudWatch log group for the Lambda function
log_group = aws.cloudwatch.LogGroup(
    f"api_{ROUTE_NAME}_lambda_log_group",
    retention_in_days=30,
)

# Add a test for the Lambda Handler
