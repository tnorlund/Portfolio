import json
import os

import pulumi
import pulumi_aws as aws

# Import the DynamoDB table name from the dynamo_db module
from dynamo_db import dynamodb_table

# Import the Lambda Layer from the lambda_layer module
from infra.components.lambda_layer import dynamo_layer
from pulumi import AssetArchive, FileArchive

# Reference the directory containing index.py
HANDLER_DIR = os.path.join(os.path.dirname(__file__), "handler")
# Get the function name from the directory name
FUNCTION_NAME = os.path.basename(os.path.dirname(__file__))
# Get the DynamoDB table name
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

# Define the IAM policy for DynamoDB access
lambda_policy = aws.iam.Policy(
    f"{FUNCTION_NAME}_lambda_policy",
    description="IAM policy for label count cache updater Lambda to access DynamoDB",
    policy=dynamodb_table.arn.apply(
        lambda dynamo_arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:Query",
                            "dynamodb:GetItem",
                            "dynamodb:PutItem",
                            "dynamodb:UpdateItem",
                            "dynamodb:DescribeTable",
                        ],
                        "Resource": [
                            dynamo_arn,
                            f"{dynamo_arn}/index/GSI1",
                            f"{dynamo_arn}/index/GSITYPE",
                        ],
                    },
                ],
            }
        )
    ),
)

# Attach the custom policy to the role
lambda_role_policy_attachment = aws.iam.RolePolicyAttachment(
    f"{FUNCTION_NAME}_lambda_policy_attachment",
    role=lambda_role.name,
    policy_arn=lambda_policy.arn,
)

# Attach the basic execution role
aws.iam.RolePolicyAttachment(
    f"{FUNCTION_NAME}_lambda_basic_execution",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)

# Create the Lambda function
label_count_cache_updater_lambda = aws.lambda_.Function(
    f"{FUNCTION_NAME}_lambda",
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
    memory_size=2048,
    timeout=300,  # 5 minutes timeout
    tags={"environment": pulumi.get_stack()},
)

# CloudWatch log group for the Lambda function
log_group = aws.cloudwatch.LogGroup(
    f"{FUNCTION_NAME}_lambda_log_group",
    name=pulumi.Output.concat("/aws/lambda/", label_count_cache_updater_lambda.name),
    retention_in_days=30,
)

# Create CloudWatch Events rule to trigger Lambda every 5 minutes
cache_update_schedule = aws.cloudwatch.EventRule(
    f"{FUNCTION_NAME}_schedule",
    description="Trigger label count cache update every 5 minutes",
    schedule_expression="rate(5 minutes)",
)

# Create CloudWatch Events target to invoke Lambda
cache_update_target = aws.cloudwatch.EventTarget(
    f"{FUNCTION_NAME}_target",
    rule=cache_update_schedule.name,
    arn=label_count_cache_updater_lambda.arn,
)

# Give CloudWatch Events permission to invoke Lambda
lambda_permission = aws.lambda_.Permission(
    f"{FUNCTION_NAME}_lambda_permission",
    action="lambda:InvokeFunction",
    function=label_count_cache_updater_lambda.name,
    principal="events.amazonaws.com",
    source_arn=cache_update_schedule.arn,
)

# Export the Lambda function ARN
pulumi.export(f"{FUNCTION_NAME}_lambda_arn", label_count_cache_updater_lambda.arn)
pulumi.export(f"{FUNCTION_NAME}_schedule_arn", cache_update_schedule.arn)
