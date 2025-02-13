import os
import json
import pulumi
import pulumi_aws as aws
from pulumi import AssetArchive, FileArchive

# Import the DynamoDB table name from the dynamo_db module
from dynamo_db import dynamodb_table

# Import the Lambda Layer from the lambda_layer module
from lambda_layer import lambda_layer

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

# Attach the necessary policies to the role
lambda_policy = aws.iam.Policy(
    f"api_{ROUTE_NAME}_lambda_policy",
    description="IAM policy for '/images' route Lambda to query DynamoDB",
    policy=dynamodb_table.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["dynamodb:Query", "dynamodb:DescribeTable"],
                        "Resource": [arn, f"{arn}/index/GSI2"],
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

# Attach the necessary policies to the role
aws.iam.RolePolicyAttachment(
    f"api_{ROUTE_NAME}_lambda_basic_execution",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)

# Create the Lambda function for the "user" route
receipt_details_lambda = aws.lambda_.Function(
    f"api_{ROUTE_NAME}_GET_lambda",
    runtime="python3.13",  # or whichever version you prefer
    role=lambda_role.arn,
    code=AssetArchive(
        {
            ".": FileArchive(HANDLER_DIR),
        }
    ),
    handler="index.handler",  # file_name.function_name
    layers=[lambda_layer.arn],
    environment={
        "variables": {
            "DYNAMODB_TABLE_NAME": DYNAMODB_TABLE_NAME,
            "ALLOWED_ORIGINS": "http://localhost:3000,https://tylernorlund.com,https://www.tylernorlund.com,https://dev.tylernorlund.com,http://192.168.4.117:3000",
        }
    },
    memory_size=1024,  # Increase RAM to 512 MB
    timeout=30,  # Increase timeout to 30 seconds
)

# CloudWatch log group for the Lambda function
log_group = aws.cloudwatch.LogGroup(
    f"api_{ROUTE_NAME}_lambda_log_group",
    retention_in_days=30,
)

# Add a test for the Lambda Handler
