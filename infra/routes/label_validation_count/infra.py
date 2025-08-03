import json
import os

import pulumi
import pulumi_aws as aws

# Import the DynamoDB table name from the dynamo_db module
from dynamo_db import dynamodb_table

# Import the Lambda Layer from the lambda_layer module
from lambda_layer import dynamo_layer
from pulumi import AssetArchive, FileArchive

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

# Get environment configuration
is_production = pulumi.get_stack() == "prod"

lambda_policy = aws.iam.Policy(
    f"api_{ROUTE_NAME}_lambda_policy",
    description="IAM policy for label validation count Lambda to query DynamoDB",
    policy=dynamodb_table.arn.apply(
        lambda table_arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:Query",
                            "dynamodb:DescribeTable"
                        ],
                        "Resource": [
                            table_arn,
                            f"{table_arn}/index/GSI1",
                            f"{table_arn}/index/GSITYPE",
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

# Attach the necessary policies to the role
aws.iam.RolePolicyAttachment(
    f"api_{ROUTE_NAME}_lambda_basic_execution",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)

# Create the Lambda function for the "user" route
label_validation_count_lambda = aws.lambda_.Function(
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
    memory_size=1536 if is_production else 512,
    timeout=30,  # Queries should complete quickly
    reserved_concurrent_executions=100 if is_production else None,
    tags={"environment": pulumi.get_stack()},
)

# CloudWatch log group for the Lambda function
log_group = aws.cloudwatch.LogGroup(
    f"api_{ROUTE_NAME}_lambda_log_group",
    retention_in_days=7 if is_production else 3,  # Reduce log storage costs
)

# Provisioned concurrency disabled to save costs
# Cold starts are acceptable for this internal API endpoint
# Uncomment below if you need guaranteed low latency
# if is_production:
#     provisioned_config = aws.lambda_.ProvisionedConcurrencyConfig(
#         f"api_{ROUTE_NAME}_provisioned_concurrency",
#         function_name=label_validation_count_lambda.name,
#         provisioned_concurrent_executions=2,
#         qualifier=label_validation_count_lambda.version
#     )

# Export Lambda details
pulumi.export(f"{ROUTE_NAME}_lambda_arn", label_validation_count_lambda.arn)
pulumi.export(f"{ROUTE_NAME}_lambda_name", label_validation_count_lambda.name)
