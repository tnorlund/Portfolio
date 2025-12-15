import json
import os

import pulumi
import pulumi_aws as aws

# Import the DynamoDB table name from the dynamo_db module
from dynamo_db import dynamodb_table

# Import Lambda layers from the lambda_layer module
from infra.components.lambda_layer import dynamo_layer
from pulumi import AssetArchive, FileArchive

# Get the directory where this file is located
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# Constants
HANDLER_DIR = os.path.join(CURRENT_DIR, "handler")

# Get the OpenAI API key and Google Places API key from the config
config = pulumi.Config()
openai_api_key = config.require_secret("OPENAI_API_KEY")
google_places_api_key = config.require_secret("GOOGLE_PLACES_API_KEY")


# Create IAM role for the Lambda functions
lambda_role = aws.iam.Role(
    "receipt_processor_lambda_role",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "sts:AssumeRole",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Effect": "Allow",
                    "Sid": "",
                }
            ],
        }
    ),
)

# Attach basic Lambda execution policy
aws.iam.RolePolicyAttachment(
    "receipt_processor_lambda_basic_execution",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)

# Create policy for DynamoDB access
dynamodb_policy = aws.iam.Policy(
    "receipt_processor_dynamodb_policy",
    description="IAM policy for receipt processor to access DynamoDB",
    policy=dynamodb_table.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:DescribeTable",
                            "dynamodb:Query",
                            "dynamodb:GetItem",
                            "dynamodb:PutItem",
                            "dynamodb:UpdateItem",
                            "dynamodb:BatchWriteItem",
                        ],
                        "Resource": [
                            arn,
                            f"{arn}/index/GSITYPE",
                        ],
                    }
                ],
            }
        )
    ),
)

# Attach DynamoDB policy to role
dynamodb_policy_attachment = aws.iam.RolePolicyAttachment(
    "receipt_processor_dynamodb_policy_attachment",
    role=lambda_role.name,
    policy_arn=dynamodb_policy.arn,
)

# Create Lambda function for listing receipts
list_receipts_lambda = aws.lambda_.Function(
    "list_receipts_lambda",
    runtime="python3.12",
    architectures=["arm64"],
    role=lambda_role.arn,
    code=AssetArchive({".": FileArchive(HANDLER_DIR)}),
    handler="list_receipts.handler",
    layers=[dynamo_layer.arn],
    memory_size=512,
    timeout=60,
    environment={"variables": {"DYNAMODB_TABLE_NAME": dynamodb_table.name}},
)

# Create Lambda function for processing individual receipts
process_receipt_lambda = aws.lambda_.Function(
    "process_receipt_lambda",
    runtime="python3.12",
    architectures=["arm64"],
    role=lambda_role.arn,
    code=AssetArchive({".": FileArchive(HANDLER_DIR)}),
    handler="process_receipt.handler",
    layers=[dynamo_layer.arn],
    memory_size=1024,
    timeout=300,  # 5 minutes
    environment={
        "variables": {
            "DYNAMODB_TABLE_NAME": dynamodb_table.name,
            "OPENAI_API_KEY": openai_api_key,
            "GOOGLE_PLACES_API_KEY": google_places_api_key,
        }
    },
)
