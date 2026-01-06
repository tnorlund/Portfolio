"""
Infrastructure for label validation timeline cache generator Lambda.

This module creates:
1. S3 bucket for storing the timeline cache
2. Lambda function for generating the cache
3. IAM roles and policies for DynamoDB read and S3 write access
4. Weekly EventBridge schedule to regenerate the cache
"""

import json
import os
from typing import Optional

import pulumi
import pulumi_aws as aws
from dynamo_db import dynamodb_table
from infra.components.lambda_layer import dynamo_layer
from pulumi import AssetArchive, FileArchive, Output, ResourceOptions

# Reference the directory containing index.py
HANDLER_DIR = os.path.join(os.path.dirname(__file__), "handler")
# Get the route name from the directory name
ROUTE_NAME = os.path.basename(os.path.dirname(__file__))
# Get the DynamoDB table name
DYNAMODB_TABLE_NAME = dynamodb_table.name

# Get stack configuration
stack = pulumi.get_stack()
is_production = stack == "prod"

# Module-level variables to hold resources
# These will be set when create_label_validation_timeline_cache is called
cache_bucket: Optional[aws.s3.Bucket] = None
cache_generator_lambda: Optional[aws.lambda_.Function] = None


def create_label_validation_timeline_cache() -> tuple[
    aws.s3.Bucket, aws.lambda_.Function
]:
    """Create the label validation timeline cache generator infrastructure.

    Returns:
        Tuple of (cache_bucket, lambda_function)
    """
    global cache_bucket, cache_generator_lambda

    # Create dedicated S3 bucket for timeline cache
    cache_bucket = aws.s3.Bucket(
        f"label-validation-timeline-cache-{stack}",
        force_destroy=not is_production,
        tags={
            "Name": f"label-validation-timeline-cache-{stack}",
            "Purpose": "LabelValidationTimelineCache",
            "Environment": stack,
            "ManagedBy": "Pulumi",
        },
    )

    # Configure bucket ownership controls
    aws.s3.BucketOwnershipControls(
        f"label-validation-timeline-cache-ownership-{stack}",
        bucket=cache_bucket.id,
        rule=aws.s3.BucketOwnershipControlsRuleArgs(
            object_ownership="BucketOwnerEnforced"
        ),
    )

    # Create IAM role for Lambda
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

    # DynamoDB read policy
    dynamodb_policy = aws.iam.RolePolicy(
        f"api_{ROUTE_NAME}_dynamodb_policy",
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

    # S3 write policy for cache bucket
    s3_policy = aws.iam.RolePolicy(
        f"api_{ROUTE_NAME}_s3_policy",
        role=lambda_role.id,
        policy=cache_bucket.id.apply(
            lambda bucket: json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "s3:PutObject",
                                "s3:GetObject",
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

    # Attach basic execution role
    aws.iam.RolePolicyAttachment(
        f"api_{ROUTE_NAME}_basic_execution",
        role=lambda_role.name,
        policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )

    # Create the Lambda function
    cache_generator_lambda = aws.lambda_.Function(
        f"api_{ROUTE_NAME}_lambda",
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
        environment=aws.lambda_.FunctionEnvironmentArgs(
            variables={
                "DYNAMODB_TABLE_NAME": DYNAMODB_TABLE_NAME,
                "S3_CACHE_BUCKET": cache_bucket.id,
            }
        ),
        memory_size=1536,  # More memory for processing many records
        timeout=300,  # 5 minutes for scanning all labels
        tags={"environment": stack},
    )

    # CloudWatch log group
    aws.cloudwatch.LogGroup(
        f"api_{ROUTE_NAME}_log_group",
        name=cache_generator_lambda.name.apply(
            lambda fn: f"/aws/lambda/{fn}"
        ),
        retention_in_days=7 if is_production else 3,
    )

    # Weekly schedule to regenerate the cache (Sundays at midnight UTC)
    cache_update_schedule = aws.cloudwatch.EventRule(
        f"{ROUTE_NAME}_schedule",
        description="Regenerate label validation timeline cache weekly",
        schedule_expression="cron(0 0 ? * SUN *)",
    )

    # EventBridge target to invoke Lambda
    aws.cloudwatch.EventTarget(
        f"{ROUTE_NAME}_target",
        rule=cache_update_schedule.name,
        arn=cache_generator_lambda.arn,
    )

    # Permission for EventBridge to invoke Lambda
    aws.lambda_.Permission(
        f"{ROUTE_NAME}_schedule_permission",
        action="lambda:InvokeFunction",
        function=cache_generator_lambda.name,
        principal="events.amazonaws.com",
        source_arn=cache_update_schedule.arn,
    )

    # Export Lambda details
    pulumi.export(f"{ROUTE_NAME}_lambda_arn", cache_generator_lambda.arn)
    pulumi.export(f"{ROUTE_NAME}_lambda_name", cache_generator_lambda.name)
    pulumi.export(f"{ROUTE_NAME}_bucket_name", cache_bucket.id)
    pulumi.export(f"{ROUTE_NAME}_schedule_arn", cache_update_schedule.arn)

    return cache_bucket, cache_generator_lambda
