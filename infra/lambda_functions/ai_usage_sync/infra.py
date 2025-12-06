"""
Infrastructure for AI usage sync Lambda function.
This function runs periodically to pull usage data from AI service providers.
"""

import json
import os

import pulumi
import pulumi_aws as aws

# Import dependencies
from dynamo_db import dynamodb_table
from infra.components.lambda_layer import dynamo_layer
from pulumi import AssetArchive, FileArchive

# Reference the directory containing handler
HANDLER_DIR = os.path.join(os.path.dirname(__file__), "handler")
FUNCTION_NAME = os.path.basename(os.path.dirname(__file__))
DYNAMODB_TABLE_NAME = dynamodb_table.name


# Define the IAM role for the Lambda function
lambda_role = aws.iam.Role(
    f"{FUNCTION_NAME}_lambda_role",
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

# Define the IAM policy for accessing various services
lambda_policy = aws.iam.Policy(
    f"{FUNCTION_NAME}_lambda_policy",
    description="IAM policy for AI usage sync Lambda",
    policy=dynamodb_table.arn.apply(
        lambda dynamo_arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        # DynamoDB permissions
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:PutItem",
                            "dynamodb:GetItem",
                            "dynamodb:Query",
                            "dynamodb:UpdateItem",
                        ],
                        "Resource": [dynamo_arn],
                    },
                    {
                        # Secrets Manager permissions (for API keys)
                        "Effect": "Allow",
                        "Action": [
                            "secretsmanager:GetSecretValue",
                        ],
                        "Resource": [
                            "arn:aws:secretsmanager:*:*:secret:openai-api-key-*",
                            "arn:aws:secretsmanager:*:*:secret:anthropic-api-key-*",
                            "arn:aws:secretsmanager:*:*:secret:google-cloud-*",
                        ],
                    },
                    {
                        # CloudWatch Metrics (for monitoring)
                        "Effect": "Allow",
                        "Action": [
                            "cloudwatch:PutMetricData",
                        ],
                        "Resource": "*",
                    },
                ],
            }
        )
    ),
)

# Attach policies to role
lambda_role_policy_attachment = aws.iam.RolePolicyAttachment(
    f"{FUNCTION_NAME}_lambda_policy_attachment",
    role=lambda_role.name,
    policy_arn=lambda_policy.arn,
)

aws.iam.RolePolicyAttachment(
    f"{FUNCTION_NAME}_lambda_basic_execution",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)

# Create the Lambda function
ai_usage_sync_lambda = aws.lambda_.Function(
    f"{FUNCTION_NAME}_lambda",
    runtime="python3.12",
    architectures=["arm64"],
    role=lambda_role.arn,
    code=AssetArchive(
        {
            ".": FileArchive(HANDLER_DIR),
        }
    ),
    handler="index.lambda_handler",
    layers=[dynamo_layer.arn],
    environment={
        "variables": {
            "DYNAMODB_TABLE_NAME": DYNAMODB_TABLE_NAME,
            # API keys should be stored in AWS Secrets Manager
            # These are referenced here for the Lambda to know which secrets to fetch
            "OPENAI_API_KEY_SECRET": "openai-api-key",
            "ANTHROPIC_API_KEY_SECRET": "anthropic-api-key",
            "GOOGLE_CLOUD_PROJECT_ID": os.environ.get(
                "GOOGLE_CLOUD_PROJECT_ID", ""
            ),
        }
    },
    memory_size=512,
    timeout=300,  # 5 minutes - API calls can be slow
    tags={"environment": pulumi.get_stack()},
)

# CloudWatch log group
log_group = aws.cloudwatch.LogGroup(
    f"{FUNCTION_NAME}_lambda_log_group",
    name=pulumi.Output.concat("/aws/lambda/", ai_usage_sync_lambda.name),
    retention_in_days=30,
)

# Create EventBridge rule to run every hour
sync_schedule = aws.cloudwatch.EventRule(
    f"{FUNCTION_NAME}_schedule",
    description="Trigger AI usage sync every hour",
    schedule_expression="rate(1 hour)",
)

# Create EventBridge target
sync_target = aws.cloudwatch.EventTarget(
    f"{FUNCTION_NAME}_target",
    rule=sync_schedule.name,
    arn=ai_usage_sync_lambda.arn,
)

# Give EventBridge permission to invoke Lambda
lambda_permission = aws.lambda_.Permission(
    f"{FUNCTION_NAME}_lambda_permission",
    action="lambda:InvokeFunction",
    function=ai_usage_sync_lambda.name,
    principal="events.amazonaws.com",
    source_arn=sync_schedule.arn,
)

# Export the Lambda function ARN
pulumi.export(f"{FUNCTION_NAME}_lambda_arn", ai_usage_sync_lambda.arn)
pulumi.export(f"{FUNCTION_NAME}_schedule_arn", sync_schedule.arn)
