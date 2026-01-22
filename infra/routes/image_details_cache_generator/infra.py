"""Pulumi infrastructure for image details cache generator Lambda."""

import json
import os

import pulumi
import pulumi_aws as aws
from pulumi import AssetArchive, FileArchive

# Import the DynamoDB table name from the dynamo_db module
from dynamo_db import dynamodb_table

# Import the Lambda Layer from the lambda_layer module
from infra.components.lambda_layer import dynamo_layer

# Reference the directory containing index.py
HANDLER_DIR = os.path.join(os.path.dirname(__file__), "handler")
# Get the route name from the directory name
ROUTE_NAME = os.path.basename(os.path.dirname(__file__))
# S3 bucket names can't have underscores, so use hyphens
BUCKET_NAME_PREFIX = ROUTE_NAME.replace("_", "-")
# Get the DynamoDB table name
DYNAMODB_TABLE_NAME = dynamodb_table.name

# Get stack configuration
stack = pulumi.get_stack()
is_production = stack == "prod"

# Create dedicated S3 bucket for the cache
cache_bucket = aws.s3.Bucket(
    f"{BUCKET_NAME_PREFIX}-cache-bucket",
    force_destroy=not is_production,  # Prevent accidental data loss in prod
    tags={
        "Name": f"{BUCKET_NAME_PREFIX}-cache-bucket",
        "Purpose": "ImageDetailsAPICache",
        "Environment": stack,
        "ManagedBy": "Pulumi",
    },
)

# Configure bucket ownership controls
aws.s3.BucketOwnershipControls(
    f"{ROUTE_NAME}-cache-bucket-ownership",
    bucket=cache_bucket.id,
    rule=aws.s3.BucketOwnershipControlsRuleArgs(
        object_ownership="BucketOwnerEnforced"
    ),
)

# Define the IAM role for the Lambda function
lambda_role = aws.iam.Role(
    f"{ROUTE_NAME}_lambda_role",
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

# DynamoDB access policy
dynamodb_policy = aws.iam.RolePolicy(
    f"{ROUTE_NAME}_dynamodb_policy",
    role=lambda_role.id,
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
                            f"{arn}/index/*",
                        ],
                    },
                ],
            }
        )
    ),
)

# S3 cache bucket read/write policy
cache_s3_policy = aws.iam.RolePolicy(
    f"{ROUTE_NAME}_cache_s3_policy",
    role=lambda_role.id,
    policy=cache_bucket.id.apply(
        lambda bucket: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "s3:GetObject",
                            "s3:PutObject",
                            "s3:ListBucket",
                        ],
                        "Resource": [
                            f"arn:aws:s3:::{bucket}/*",
                            f"arn:aws:s3:::{bucket}",
                        ],
                    },
                ],
            }
        )
    ),
)

# Attach basic Lambda execution policy
aws.iam.RolePolicyAttachment(
    f"{ROUTE_NAME}_lambda_basic_execution",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)

# Create the Lambda function
image_details_cache_generator_lambda = aws.lambda_.Function(
    f"{ROUTE_NAME}_lambda",
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
            "S3_CACHE_BUCKET": cache_bucket.id,
        }
    },
    memory_size=1024,
    timeout=60,  # 1 minute should be plenty for 40 images
    tags={"environment": stack},
)

# CloudWatch log group for the Lambda function
log_group = aws.cloudwatch.LogGroup(
    f"{ROUTE_NAME}_lambda_log_group",
    name=image_details_cache_generator_lambda.name.apply(
        lambda function_name: f"/aws/lambda/{function_name}"
    ),
    retention_in_days=30,
)

# EventBridge schedule to run once per day
schedule = aws.cloudwatch.EventRule(
    f"{ROUTE_NAME}_schedule",
    description="Trigger image details cache generation once per day",
    schedule_expression="rate(1 day)",
)

# EventBridge target
event_target = aws.cloudwatch.EventTarget(
    f"{ROUTE_NAME}_event_target",
    rule=schedule.name,
    arn=image_details_cache_generator_lambda.arn,
)

# Lambda permission for EventBridge
lambda_permission = aws.lambda_.Permission(
    f"{ROUTE_NAME}_eventbridge_permission",
    action="lambda:InvokeFunction",
    function=image_details_cache_generator_lambda.name,
    principal="events.amazonaws.com",
    source_arn=schedule.arn,
)

# Export the cache bucket name for use by the API Lambda
cache_bucket_name = cache_bucket.id
