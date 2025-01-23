import os
import json
import pulumi
import pulumi_aws as aws
from pulumi import AssetArchive, FileArchive

# Reference the directory containing index.py
HANDLER_DIR = os.path.join(os.path.dirname(__file__), "handler")
# Get the route name from the directory name
ROUTE_NAME = os.path.basename(os.path.dirname(__file__))


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

# Attach the necessary policies to the role
aws.iam.RolePolicyAttachment(
    f"api_{ROUTE_NAME}_lambda_basic_execution",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)

# Create the Lambda function for the "user" route
health_check_lambda = aws.lambda_.Function(
    f"api_{ROUTE_NAME}_GET_lambda",
    runtime="python3.13",  # or whichever version you prefer
    role=lambda_role.arn,
    code=AssetArchive(
        {
            ".": FileArchive(HANDLER_DIR),
        }
    ),
    handler="index.handler",  # file_name.function_name
)

# CloudWatch log group for the Lambda function
log_group = aws.cloudwatch.LogGroup(
    f"api_{ROUTE_NAME}_lambda_log_group",
    retention_in_days=30,
)
